import asyncio
import json
import re
from pathlib import Path
from typing import Any
from google import genai
from src.core.models import TaskRecord
from src.utils.sim_runner import LogisimEmulator
from src.utils.ai_utils import generate_content_with_tools


class VerificationAgent:
    """具备动态生成 WebSocket JSON API 动作序列的验证智能体 (Headless API 版)"""

    def __init__(self, config, client: genai.Client, cache=None):
        self.config = config
        self.client = client
        self.cache = cache
        self.emulator = None
        self.project_root = Path(__file__).parents[2]
        self.prompt_dir = self.project_root / "prompts"

    def _load_prompt(self, path: Path, **kwargs) -> str:
        """读取并格式化提示词"""
        if not path.exists():
            return f"Prompt file not found: {path}"
        content = path.read_text(encoding="utf-8")
        for k, v in kwargs.items():
            content = content.replace(f"{{{k}}}", str(v))
        return content

    def _sanitize_filename(self, filename: str, replacement: str = "-") -> str:
        # 正则模式匹配所有非法字符
        # 注意：反斜杠 \ 需要在正则中转义为 \\
        pattern = r'[<>:"/\\|?*\x00-\x1F]'

        # 将非法字符替换为你指定的分隔符（默认替换为 "-"）
        sanitized = re.sub(pattern, replacement, filename)

        # 可选优化：去除两端的空格和点（Windows 不允许文件夹以点或空格结尾）
        return sanitized.strip(" .")

    def _resolve_task_doc_paths(self, docs: list[str] | None) -> list[Path]:
        """将任务关联文档路径统一解析为存在的绝对路径。"""
        if not docs:
            return []

        resolved: list[Path] = []
        seen: set[str] = set()

        for raw in docs:
            if not raw:
                continue

            p = Path(raw)
            candidates = [
                p,
                self.project_root / p,
                self.project_root / "workspace" / p.name,
                Path("workspace") / p.name,
            ]

            for c in candidates:
                c_abs = c.absolute()
                key = str(c_abs)
                if key in seen:
                    continue
                if c_abs.exists() and c_abs.is_file():
                    resolved.append(c_abs)
                    seen.add(key)
                    break

        return resolved

    def _build_task_docs_prompt_block(self, docs_abs: list[Path]) -> str:
        """构造任务级文件提示块，显式给出 load_memory 可用绝对路径。"""
        if not docs_abs:
            return ""

        lines = ["### 当前任务参考文件（绝对路径）"]
        for p in docs_abs:
            lines.append(f"- {p}")

        txt_hint = []
        for p in docs_abs:
            if p.suffix.lower() == ".txt":
                try:
                    first_line = p.read_text(
                        encoding="utf-8", errors="ignore"
                    ).splitlines()
                    head = first_line[0].strip() if first_line else ""
                    if head:
                        txt_hint.append(f"- {p.name} 首行: {head[:80]}")
                except Exception:
                    continue

        lines.append("请优先从以上路径中选择 load_memory 的 txt_path，不要臆造文件名。")
        if txt_hint:
            lines.append("\n可用 txt 预览：")
            lines.extend(txt_hint[:5])

        return "\n".join(lines)

    async def run(self, task: TaskRecord, circ_path: Path) -> TaskRecord:
        """执行验证流程 (WebSocket 版)"""
        # 0. 检查缓存
        if self.cache:
            cached = self.cache.get_task_if_done(task)
            if cached:
                return cached

        if not self.emulator:
            self.emulator = LogisimEmulator(self.config, self.client)

        print(
            f"[Verification] 任务: {task.task_name}，目标子电路: {task.target_subcircuit}"
        )
        print(f"[Verification] 正在连接并加载电路: {circ_path}")
        success = await self.emulator.launch_and_initialize(str(circ_path))
        if not success:
            task.status = "failed"
            task.analysis_raw = (
                "无法连接或加载电路，请确保后端服务 (ws://localhost:9924/ws) 正在运行。"
            )
            return task

        save_dir = Path("output") / "实验报告.assets"
        save_dir.mkdir(parents=True, exist_ok=True)

        max_retries = 9
        last_error = None
        last_error_step = None
        blueprint = None
        failed_item = None
        retry_feedback_history = []
        prev_circuit_screenshot: bytes | None = None  # 上一轮失败时的电路截图（内存中）
        prev_selected_circuit: str | None = None  # 上一轮选择的子电路名称

        for attempt in range(max_retries + 1):
            if attempt > 0:
                print(f"[Verification] 正在进行第 {attempt} 次自修复重试...")
                # 环境重置：完全关闭并重新连接
                self.emulator.close()
                await self.emulator.launch_and_initialize(str(circ_path))

            # 1. 获取基础上下文
            circuits_resp = await self.emulator.send_command("get_circuits")
            circuit_list = (
                circuits_resp.get("payload", [])
                if isinstance(circuits_resp, dict)
                else []
            )

            # 2. 识别并切换到目标子电路；每次均由 LLM 决定，重试时附带上轮电路截图辅助判断
            selected_circuit = await self._identify_and_switch_circuit(
                task, circuit_list, screenshot_bytes=prev_circuit_screenshot
            )

            io_resp = await self.emulator.send_command("get_io")
            io_info = io_resp.get("payload", {}) if isinstance(io_resp, dict) else {}
            reuse_prev_end_screenshot = (
                attempt > 0
                and prev_circuit_screenshot is not None
                and selected_circuit is not None
                and selected_circuit == prev_selected_circuit
            )
            if reuse_prev_end_screenshot:
                screenshot = prev_circuit_screenshot
                print("[Verification] 本轮电路未变化，复用上一轮结束截图。")
            else:
                snap = await self.emulator.send_command(
                    "get_screenshot", width=1280, height=720
                )
                screenshot = (
                    snap["binary"]
                    if isinstance(snap, dict) and snap.get("status") == "ok"
                    else None
                )

            prev_selected_circuit = selected_circuit

            # 3. 使用 blueprint 生成指令；失败后带反馈进入下一轮重生成
            retry_feedback = (
                "\n\n".join(retry_feedback_history) if retry_feedback_history else ""
            )
            blueprint = await self._generate_api_blueprint(
                task,
                io_info,
                circuit_list,
                retry_feedback=retry_feedback,
                screenshot_bytes=screenshot,
            )

            # 4. 执行指令序列
            print(f"[Verification] 开始执行 API 指令序列 (尝试 {attempt+1})...")
            failed_item = None
            for item in blueprint:
                action = item.get("action")
                print(f"[Run API] >> {item}")
                kwargs = {
                    k: v
                    for k, v in item.items()
                    if k not in ("action", "step", "reason")
                }

                resp = await self.emulator.send_command(action, **kwargs)
                if isinstance(resp, dict) and resp.get("status") == "error":
                    failed_item = item
                    last_error = resp.get("message")
                    last_error_step = f"API: {action} with args {kwargs}"
                    print(
                        f"[API Error] 指令执行失败: {last_error} (步骤: {last_error_step})"
                    )
                    break

            if not failed_item:
                print("[Verification] 指令序列全量执行成功。")
                break

            # 重置前抓取当前电路截图，供下一轮 LLM 重新选电路时参考（不保存为文件）
            snap = await self.emulator.send_command(
                "get_screenshot", width=1280, height=720
            )
            if (
                isinstance(snap, dict)
                and snap.get("status") == "ok"
                and "binary" in snap
            ):
                prev_circuit_screenshot = snap["binary"]
                print("[Verification] 已抓取当前电路截图，供下一轮重新选择电路使用。")
            else:
                prev_circuit_screenshot = None

            retry_feedback_history.append(
                self._build_blueprint_retry_feedback(
                    attempt, failed_item, f"{last_error} (步骤: {last_error_step})"
                )
            )

        if failed_item:
            task.status = "failed"
            task.analysis_raw = (
                f"API 指令序列执行失败，已达重试上限。错误: {last_error}"
            )
            raise RuntimeError(
                f"Verification failed after {max_retries} attempts. Last error: {last_error}"
            )

        # 5. 最终抓图与验证
        print("[Verification] 正在保存最终状态截图...")
        snap_resp = await self.emulator.send_command(
            "get_screenshot", width=1920, height=1080
        )

        filtered_taskname = self._sanitize_filename(task.task_name)
        output_path = save_dir / f"{filtered_taskname}.png"
        if (
            isinstance(snap_resp, dict)
            and snap_resp.get("status") == "ok"
            and "binary" in snap_resp
        ):
            output_path.write_bytes(snap_resp["binary"])
            task.assets.append(str(output_path))
        else:
            print("[Warning] 获取截图失败。")

        import PIL.Image

        if output_path.exists():
            explanation = await self._generate_wrapped_analysis_with_retry(
                image=PIL.Image.open(output_path),
                task_desc=task.analysis_raw,
                max_retries=2,
            )
        else:
            explanation = "未获得有效截图输出。"

        task.status = "finished"
        task.analysis_raw = explanation

        # 4. 保存缓存
        if self.cache:
            self.cache.save_task(task)

        return task

    async def _generate_api_blueprint(
        self,
        task: TaskRecord,
        io_info,
        circuit_list: list[Any],
        retry_feedback: str = "",
        screenshot_bytes: bytes | None = None,
    ) -> list[dict[str, Any]]:
        """映射任务为 API 动作脚本：包含完整的 API 协议与电路上下文"""
        prompt_path = self.prompt_dir / "verification" / "blueprint.txt"

        prompt = self._load_prompt(
            prompt_path,
            goal=task.analysis_raw,
            task_name=task.task_name,
            io_input=json.dumps(io_info["inputs"], ensure_ascii=False),
            io_output=json.dumps(io_info["outputs"], ensure_ascii=False),
            io_all=json.dumps(io_info["all_labeled"], ensure_ascii=False),
            circuit_list=json.dumps(circuit_list, ensure_ascii=False),
            target_subcircuit=task.target_subcircuit or "未指定",
        )
        if retry_feedback:
            prompt += (
                "\n\n### 🔁 多轮失败反馈\n"
                + retry_feedback
                + "\n\n请根据以上失败原因重新生成一套完整 JSON 动作序列，"
                + "避免重复触发相同错误。当前模拟器已重置到初始状态，"
                + "并且系统已重新切换到目标子电路；不要输出 switch_circuit。"
            )

        task_doc_paths = self._resolve_task_doc_paths(task.task_instruction_docs)
        docs_block = self._build_task_docs_prompt_block(task_doc_paths)
        if docs_block:
            prompt += "\n\n" + docs_block

        if screenshot_bytes:
            import io
            import PIL.Image

            contents: str | list[Any] = [
                PIL.Image.open(io.BytesIO(screenshot_bytes)),
                prompt
                + "\n\n### 当前电路截图\n"
                + "上图是已经切换到目标子电路后的当前画面。"
                + "请结合截图中的元件连线、标签与 IO 布局生成验证动作序列。",
            ]
        else:
            contents = prompt

        return await self._call_llm_for_blueprint(contents)

    def _build_blueprint_retry_feedback(
        self,
        attempt: int,
        failed_item: dict[str, Any] | None,
        error_msg: str | None,
    ) -> str:
        """构造注入 blueprint 的失败反馈，形成多轮对话上下文。"""
        step = json.dumps(failed_item or {}, ensure_ascii=False)
        reason = error_msg or "未知错误"
        return (
            f"第 {attempt + 1} 轮执行失败\n"
            + f"- 失败步骤: {step}\n"
            + f"- 失败原因: {reason}\n"
            + "- 当前状态: 模拟器已重置到初始状态，并已重新切换到目标子电路。"
        )

    async def _identify_and_switch_circuit(
        self,
        task: TaskRecord,
        circuit_list: list[Any],
        screenshot_bytes: bytes | None = None,
    ) -> str | None:
        """使用 Gemini Flash 确定并切换到目标电路，返回切换成功的电路名称（失败时返回 None）"""
        import io
        import PIL.Image

        prompt_path = self.prompt_dir / "verification" / "switch.txt"
        prompt_text = self._load_prompt(
            prompt_path,
            task_name=task.task_name,
            goal=task.analysis_raw,
            target_subcircuit=task.target_subcircuit or "未指定",
            circuit_list=json.dumps(circuit_list, ensure_ascii=False),
        )

        if screenshot_bytes:
            # 将上一轮电路截图和提示词一起发给 LLM，辅助重新判断
            img = PIL.Image.open(io.BytesIO(screenshot_bytes))
            contents = [
                img,
                prompt_text
                + "\n\n⚠️ 注意：上图是上一轮选择的电路截图，说明该电路可能选错了。"
                + "请结合截图重新判断，选择真正符合任务的目标子电路。",
            ]
        else:
            contents = prompt_text

        response = await generate_content_with_tools(
            self.client,
            model=self.config.gemini.model_flash,
            contents=contents,
            config={"response_mime_type": "application/json"},
        )

        try:
            raw = response.text
            extracted = self._extract_json(raw)
            if extracted:
                cmd = json.loads(extracted)
                if isinstance(cmd, list):
                    cmd = cmd[0]

                action = cmd.get("action")
                name = cmd.get("name")
                if action == "switch_circuit" and name:
                    print(f"[Verification] 正在切换到电路: {name}")
                    await self.emulator.send_command("switch_circuit", name=name)
                    return name
                else:
                    print(f"[Warning] 识别出的切换指令无效: {cmd}")
            else:
                print(f"[Warning] 无法从 LLM 响应中提取切换指令: {raw}")
        except Exception as e:
            print(f"[Error] 执行电路识别切换失败: {e}")
        return None

    async def _call_llm_for_blueprint(
        self, contents: str | list[Any], max_json_retries: int = 2
    ) -> list[dict[str, Any]]:
        """调用 LLM 生成指令序列，并包含 JSON 自修复逻辑"""
        import json as _json

        response = await generate_content_with_tools(
            self.client,
            model=self.config.gemini.model_pro,
            contents=contents,
            config={"response_mime_type": "application/json"},
        )
        raw = response.text

        for attempt in range(max_json_retries + 1):
            extracted = self._extract_json(raw)
            if extracted:
                try:
                    data = _json.loads(extracted)
                    return data if isinstance(data, list) else [data]
                except Exception:
                    pass

            if attempt < max_json_retries:
                print(
                    f"[JSON 自修复] Blueprint 生成第 {attempt+1} 次失败，请求 LLM 修复..."
                )
                repair_prompt = (
                    "以下输出不是合法的 JSON 指令数组，请修正并重新输出，不要包含任何解释文字：\n\n"
                    + raw[:2000]
                )
                response = await generate_content_with_tools(
                    self.client,
                    model=self.config.gemini.model_pro,
                    contents=repair_prompt,
                    config={"response_mime_type": "application/json"},
                )
                raw = response.text
            else:
                print(
                    f"[Agent Parsing Error] 达到最大重试次数，无法解析 JSON: {raw[:200]}"
                )

        return []

    def _extract_json(self, text: str) -> str:
        """从杂乱文本中提取最可能的 JSON 数组或对象"""
        import json as _json
        import re as _re

        text = text.strip()

        # 1. 尝试直接解析
        try:
            _json.loads(text)
            return text
        except:
            pass

        # 2. 尝试提取 ```json ... ``` 或简单的 [ ... ] / { ... }
        for pattern in (r"```json\s*(.*?)\s*```", r"(\[.*\])", r"(\{.*\})"):
            m = _re.search(pattern, text, _re.DOTALL)
            if m:
                candidate = m.group(1).strip()
                try:
                    _json.loads(candidate)
                    return candidate
                except:
                    pass
        return ""

    async def _generate_wrapped_analysis_with_retry(
        self, image, task_desc: str, max_retries: int = 2
    ) -> str:
        """生成严格包裹格式的分析文本；不符合格式时报错并重试。"""
        base_prompt = (
            "请根据截图与任务描述输出实验分析，必须严格使用以下包裹格式：\n"
            "--BEGIN--\n"
            "这里写分析正文（可用 Markdown，但不要包含 ### 标题）\n"
            "--END--\n\n"
            f"任务描述：{task_desc}"
        )

        last_error = ""
        last_raw = ""
        for attempt in range(max_retries + 1):
            if attempt == 0:
                contents = [image, base_prompt]
            else:
                contents = [
                    image,
                    (
                        "你上一次输出不符合格式要求。请只输出严格包裹格式：\n"
                        "--BEGIN--\n"
                        "正文（可用 Markdown，但不要包含 ### 标题）\n"
                        "--END--\n\n"
                        f"上一次错误：{last_error}\n"
                        f"上一次输出：\n{last_raw[:1500]}\n\n"
                        f"任务描述：{task_desc}"
                    ),
                ]

            response = await generate_content_with_tools(
                self.client,
                model=self.config.gemini.model_pro,
                contents=contents,
            )
            raw = (response.text or "").strip()

            try:
                return self._extract_wrapped_analysis(raw, strict=True)
            except ValueError as e:
                last_error = str(e)
                last_raw = raw
                print(
                    f"[Verification] 分析格式校验失败（第 {attempt+1} 次），准备重试: {e}"
                )

        raise RuntimeError(
            "实验分析生成失败：模型多次未按 --BEGIN--/--END-- 包裹格式返回。"
        )

    def _extract_wrapped_analysis(self, text: str, strict: bool = False) -> str:
        """提取 --BEGIN-- 与 --END-- 之间的正文；strict=True 时未命中直接报错。"""
        if not text:
            if strict:
                raise ValueError("空响应，未包含包裹格式")
            return ""
        match = re.search(r"--BEGIN--\s*(.*?)\s*--END--", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        if strict:
            raise ValueError("未找到 --BEGIN--/--END-- 包裹格式")
        return text.strip()

    def close(self):
        if self.emulator:
            self.emulator.close()
