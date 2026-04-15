import asyncio
import io
import re
import json
import shutil
from pathlib import Path
import PIL.Image

from ..utils.ai_utils import generate_content_with_tools
from ..core.models import TaskRecord
from ..utils.sim_runner import LogisimEmulator
from .verification_agent import VerificationAgent


class DesignAgent:
    """
    EDA-AI 设计性实验智能体
    职责：
    1. 参考电路截图：通过 LogisimEmulator (WebSocket) 打开并截图。
    2. 电路重命名与归档：将待提交电路拷贝至 output/提交电路/ 并按任务名命名。
    3. 任务拆解：调用 LLM 将设计要求拆解为细粒度的验证子任务。
    """

    def __init__(
        self,
        client,
        config,
        model_flash: str,
        verification_agent: VerificationAgent | None = None,
        cache=None,
    ):
        self.client = client
        self.config = config
        self.model_flash = model_flash
        self.verification_agent = verification_agent
        self.cache = cache
        self.project_root = Path(__file__).parents[2]
        self.prompt_dir = self.project_root / "prompts"

    async def run(
        self,
        task: TaskRecord,
        source_circ_path: Path | None,
        reference_circ_path: Path | None,
    ) -> tuple[TaskRecord, list[TaskRecord]]:
        """主入口：执行截图、拷贝和拆解。"""
        print(f"\n[DesignAgent] 处理任务: {task.task_name}")

        # 0. 检查缓存
        if self.cache:
            cached_main = self.cache.get_task_if_done(task)
            cached_subs = self.cache.load_design_subtasks(task.task_id)
            if cached_main and cached_subs is not None:
                return cached_main, cached_subs

        # 1. 参考电路截图
        if reference_circ_path and reference_circ_path.exists():
            await self._screenshot_reference(task, reference_circ_path)

        # 2. 拷贝并重命名参考电路（REF）至提交目录
        if reference_circ_path and reference_circ_path.exists():
            self._copy_target_circuit(task, reference_circ_path)

        # 3. 细粒度任务拆解
        sub_tasks = await self._decompose_to_subtasks(task)

        # 4. 在 DesignAgent 内部执行子任务验证
        verified_sub_tasks = await self._run_verification_subtasks(
            sub_tasks, source_circ_path, reference_circ_path
        )

        # 5. 合并所有子任务图片和文本，生成设计实验分析
        merged_analysis = await self._generate_merged_design_analysis(
            task, verified_sub_tasks
        )
        if merged_analysis:
            task.analysis_raw = merged_analysis

        task.status = "finished"

        # 6. 保存缓存
        if self.cache:
            self.cache.save_task(task)
            self.cache.save_design_subtasks(task.task_id, verified_sub_tasks)

        return task, verified_sub_tasks

    async def _screenshot_reference(self, task: TaskRecord, ref_path: Path):
        """打开参考电路并截图，保存至 output/实验报告.assets/"""
        emulator = LogisimEmulator(self.config, self.client)
        print(f"[DesignAgent] 正在截图参考电路: {ref_path}")

        # 确保使用绝对路径
        abs_ref = ref_path.absolute()
        success = await emulator.launch_and_initialize(str(abs_ref))
        if not success:
            raise RuntimeError(
                f"Logisim WebSocket 服务未运行或无法加载参考电路: {ref_path}"
            )

        try:
            # 在截图前先识别并切换到目标电路
            circuits_resp = await emulator.send_command("get_circuits")
            payload = circuits_resp.get("payload", [])
            circuit_list = payload if isinstance(payload, list) else []
            await self._identify_and_switch_circuit(task, emulator, circuit_list)

            # 确保资产目录存在
            assets_dir = Path("output") / "实验报告.assets"
            assets_dir.mkdir(parents=True, exist_ok=True)

            save_path = assets_dir / f"reference_{task.task_name}.png"

            # 使用现有接口获取截图
            snap_resp = await emulator.send_command(
                "get_screenshot", width=1920, height=1080
            )
            if (
                isinstance(snap_resp, dict)
                and snap_resp.get("status") == "ok"
                and "binary" in snap_resp
            ):
                save_path.write_bytes(snap_resp["binary"])
                # 将截图路径插入 assets 列表的第一位
                task.assets.insert(0, str(save_path))
                print(f"[DesignAgent] 参考电路截图已保存: {save_path}")
            else:
                print(f"[DesignAgent] 警告: 获取参考电路截图失败: {snap_resp}")
        finally:
            emulator.close()

    def _copy_target_circuit(self, task: TaskRecord, source_path: Path):
        """将电路拷贝到 output/提交电路/ 并命名"""
        submit_dir = Path("output") / "提交电路"
        submit_dir.mkdir(parents=True, exist_ok=True)

        target_path = (submit_dir / f"{task.task_name}.circ").absolute()
        shutil.copy2(source_path, target_path)

        # 更新任务记录中的源码路径为新的绝对命名路径
        task.source_circ = [str(target_path)]
        print(f"[DesignAgent] 电路已归档: {target_path}")

    async def _identify_and_switch_circuit(
        self, task: TaskRecord, emulator: LogisimEmulator, circuit_list: list[str]
    ):
        """使用 Flash 模型识别截图目标电路并执行切换。"""
        prompt_path = self.prompt_dir / "verification" / "switch.txt"
        if not prompt_path.exists():
            print(
                f"[DesignAgent] 警告: 找不到切换 Prompt 文件 {prompt_path}，跳过切换。"
            )
            return

        prompt_tmpl = prompt_path.read_text(encoding="utf-8")
        goal_text = task.analysis_raw or task.section_text or "截图参考电路"
        prompt = (
            prompt_tmpl.replace("{task_name}", task.task_name or "")
            .replace("{goal}", goal_text)
            .replace("{target_subcircuit}", task.target_subcircuit or "未指定")
            .replace("{circuit_list}", json.dumps(circuit_list, ensure_ascii=False))
        )

        response = await generate_content_with_tools(
            self.client,
            model=self.model_flash,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )

        raw = (response.text or "").strip()
        extracted = self._extract_json(raw)
        if not extracted:
            print(f"[DesignAgent] 警告: 无法从 LLM 响应中提取切换指令: {raw[:200]}")
            return

        try:
            cmd = json.loads(extracted)
            if isinstance(cmd, list):
                cmd = cmd[0] if cmd else {}
            if not isinstance(cmd, dict):
                print(f"[DesignAgent] 警告: 识别出的切换指令不是对象: {cmd}")
                return

            action = cmd.get("action")
            name = cmd.get("name")
            if action == "switch_circuit" and name:
                print(f"[DesignAgent] 正在切换到电路: {name}")
                resp = await emulator.send_command("switch_circuit", name=name)
                if not isinstance(resp, dict) or resp.get("status") != "ok":
                    print(f"[DesignAgent] 警告: 切换电路失败: {resp}")
            else:
                print(f"[DesignAgent] 警告: 识别出的切换指令无效: {cmd}")
        except Exception as e:
            print(f"[DesignAgent] 警告: 执行电路切换失败: {e}")

    def _extract_json(self, text: str) -> str:
        """从模型输出中提取 JSON 对象或数组。"""
        text = (text or "").strip()
        if not text:
            return ""

        try:
            json.loads(text)
            return text
        except Exception:
            pass

        for pattern in (r"```json\s*(.*?)\s*```", r"(\{.*\})", r"(\[.*\])"):
            m = re.search(pattern, text, re.DOTALL)
            if not m:
                continue
            candidate = m.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except Exception:
                continue

        return ""

    async def _decompose_to_subtasks(self, task: TaskRecord) -> list[TaskRecord]:
        """调用 LLM 进行细粒度验证任务拆解"""
        prompt_path = self.prompt_dir / "design" / "decompose.txt"
        if not prompt_path.exists():
            print(f"[DesignAgent] 错误: 找不到 Prompt 文件 {prompt_path}")
            return []

        prompt_tmpl = prompt_path.read_text(encoding="utf-8")
        prompt = (
            prompt_tmpl.replace("{task_name}", task.task_name or "")
            .replace("{section_text}", task.section_text or "")
            .replace("{analysis_raw}", task.analysis_raw or "")
        )

        print("[DesignAgent] 正在拆解细粒度验证任务...")

        # 使用 Flash 模型进行拆解
        response = await generate_content_with_tools(
            self.client,
            model=self.model_flash,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )

        raw_content = response.text.strip()

        # 提取 JSON 内容
        json_str = ""
        md_match = re.search(r"```json\s*(.*?)\s*```", raw_content, re.DOTALL)
        if md_match:
            json_str = md_match.group(1).strip()
        else:
            bracket_match = re.search(r"(\[.*\])", raw_content, re.DOTALL)
            if bracket_match:
                json_str = bracket_match.group(1).strip()
            else:
                json_str = raw_content

        if not json_str:
            print(
                f"[DesignAgent] 警告: LLM 返回内容无法识别为 JSON 列表: {raw_content[:200]}"
            )
            return []

        try:
            items = json.loads(json_str)
            sub_tasks = []
            for item in items:
                sub_task = TaskRecord(
                    task_name=item.get("task_name", f"{task.task_name} - 子任务"),
                    task_type="verification",
                    # 继承父任务的电路（即归档后的命名电路）
                    source_circ=task.source_circ,
                    analysis_raw=item.get("description", ""),
                    # 继承原始 info 文字
                    section_text=task.section_text,
                    target_subcircuit=task.target_subcircuit,
                    experiment_objective=task.experiment_objective,
                    problem_answers=task.problem_answers.copy(),
                )
                sub_tasks.append(sub_task)
            print(f"[DesignAgent] 成功拆解出 {len(sub_tasks)} 个验证子任务。")
            return sub_tasks
        except Exception as e:
            print(f"[DesignAgent] 错误: JSON 解析失败: {e}\n原文: {json_str[:200]}")
            return []

    async def _run_verification_subtasks(
        self,
        sub_tasks: list[TaskRecord],
        source_circ_path: Path | None,
        reference_circ_path: Path | None,
    ) -> list[TaskRecord]:
        """在 DesignAgent 内部执行 verification_agent，避免 main.py 再次调度。"""
        if not self.verification_agent:
            print("[DesignAgent] 警告: 未注入 VerificationAgent，跳过子任务验证。")
            return sub_tasks

        # 设计任务历史逻辑：优先在参考电路上验证
        run_circ = reference_circ_path or source_circ_path
        if not run_circ:
            print("[DesignAgent] 警告: 无可用电路路径，跳过子任务验证。")
            return sub_tasks

        verified: list[TaskRecord] = []
        for sub in sub_tasks:
            try:
                sub = await self.verification_agent.run(sub, run_circ)
            except Exception as e:
                sub.status = "failed"
                sub.analysis_raw = f"验证执行异常: {e}"
                print(f"[DesignAgent] 子任务验证失败: {sub.task_name} -> {e}")
            verified.append(sub)
        return verified

    async def _generate_merged_design_analysis(
        self, task: TaskRecord, verified_sub_tasks: list[TaskRecord]
    ) -> str:
        """将所有子任务验证图文内容合并，生成设计/挑战实验的统一分析结论。"""
        if not verified_sub_tasks:
            return task.analysis_raw

        text_blocks = []
        images = []
        for idx, sub in enumerate(verified_sub_tasks, 1):
            text_blocks.append(
                f"[{idx}] 子任务: {sub.task_name}\n"
                f"状态: {sub.status}\n"
                f"分析: {sub.analysis_raw}\n"
            )
            for asset in sub.assets:
                p = Path(asset)
                if p.exists() and p.suffix.lower() in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                }:
                    try:
                        images.append(PIL.Image.open(io.BytesIO(p.read_bytes())))
                    except Exception:
                        continue

        prompt = (
            "你是数字电路实验报告助手。请基于以下所有子任务验证信息，"
            "输出一段合并后的'实验分析'正文。\n"
            "要求：\n"
            "1) 必须严格使用以下包裹格式输出：\n"
            "--BEGIN--\n"
            "正文（可用 Markdown，但不要包含 ### 标题）\n"
            "--END--\n"
            "2) 要覆盖关键验证现象、正确性结论、异常点（如果有）。\n"
            "3) 不要输出多余前后缀。\n\n"
            f"设计任务: {task.task_name}\n"
            f"实验目标: {task.experiment_objective}\n\n"
            "子任务文本信息:\n" + "\n".join(text_blocks)
        )

        try:
            return await self._generate_wrapped_analysis_with_retry(
                images=images,
                prompt=prompt,
                max_retries=2,
            )
        except Exception as e:
            print(f"[DesignAgent] 合并实验分析生成失败: {e}")
            return task.analysis_raw

    async def _generate_wrapped_analysis_with_retry(
        self, images: list, prompt: str, max_retries: int = 2
    ) -> str:
        """生成严格包裹格式分析；不符合格式时报错并重试。"""
        last_error = ""
        last_raw = ""

        for attempt in range(max_retries + 1):
            if attempt == 0:
                contents = images + [prompt] if images else prompt
            else:
                fix_prompt = (
                    "你上一次输出不符合格式要求。请只输出严格包裹格式：\n"
                    "--BEGIN--\n"
                    "正文（可用 Markdown，但不要包含 ### 标题）\n"
                    "--END--\n\n"
                    f"上一次错误：{last_error}\n"
                    f"上一次输出：\n{last_raw[:1500]}\n\n"
                    "请重新输出。"
                )
                contents = (
                    images + [fix_prompt, prompt]
                    if images
                    else f"{fix_prompt}\n\n{prompt}"
                )

            resp = await generate_content_with_tools(
                self.client,
                model=self.config.gemini.model_pro,
                contents=contents,
            )
            raw = (resp.text or "").strip()

            try:
                return self._extract_wrapped_analysis(raw, strict=True)
            except ValueError as e:
                last_error = str(e)
                last_raw = raw
                print(
                    f"[DesignAgent] 合并分析格式校验失败（第 {attempt+1} 次），准备重试: {e}"
                )

        raise RuntimeError(
            "合并实验分析生成失败：模型多次未按 --BEGIN--/--END-- 包裹格式返回。"
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
