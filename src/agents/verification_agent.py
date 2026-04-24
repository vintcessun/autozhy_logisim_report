"""Verification Agent (ReAct + Function Calling 版)。

重构要点（替代原 ToT/多分支 blueprint 方案）：
- 模型不再一次性生成完整动作序列，而是在 ReAct 循环里
  Thought -> Action(tool call) -> Observation 逐步推进；
- 所有硬件操作都通过绑定到 emulator 的工具暴露给模型（function calling）；
- 终止由模型主动调用 `report_verdict` 工具触发，Token 消耗更低且每一步
  都基于电路真实状态而非模型内部预测。
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import PIL.Image

from src.core.models import TaskRecord
from src.utils.ai_utils import generate_content_with_tools, generate_react_native
from src.utils.llm_client import create_genai_client
from src.utils.sim_runner import LogisimEmulator


class VerificationAgent:
    """基于 ReAct + Function Calling 的验证智能体。"""

    # ReAct 外层重试（模型未给出判定时）
    _OUTER_RETRIES = 2
    # 单次 ReAct 会话内允许的最大工具调用轮数（经由 AFC 预算控制）
    _MAX_TOOL_ROUNDS = 128
    # 软性警告阈值：达到后每个工具 Observation 都追加 must-report 警告
    _TOOL_WARN_THRESHOLD = 30
    # 硬性阈值：超过后下一次工具调用强制替换为 report_verdict(false)
    _TOOL_HARD_LIMIT = 50
    # 子电路识别/切换重试次数（不含首次）
    _SWITCH_RETRIES = 2
    # 整体目标校验重试次数（不含首次）：截图 + 分析 送审不过 → 删档重跑
    _MAX_GOAL_RETRIES = 9

    def __init__(self, config, client: Any, cache=None):
        self.config = config
        self.client = client
        self.cache = cache
        self.emulator: LogisimEmulator | None = None
        self.project_root = Path(__file__).parents[2]
        self.prompt_dir = self.project_root / "prompts"

        # ReAct 会话内可变状态
        self._verdict: dict[str, Any] | None = None
        self._tool_trace: list[dict[str, Any]] = []
        self._tool_call_count: int = 0

        # 原生 google-genai 客户端（仅用于 ReAct 工具循环）。
        # 原因：OpenAI 兼容代理会丢失 Gemini thinking 模型的
        # `thought_signature`，导致多轮 tool_call 报 400。原生 SDK
        # 通过保留 Content 对象自动回传签名。
        self._genai_client = None
        try:
            self._genai_client = create_genai_client(
                api_key=self.config.gemini.api_key,
                base_url=getattr(self.config.gemini, "base_url", None),
            )
        except Exception as exc:
            print(
                f"[Verification] 原生 Gemini 客户端创建失败，将回退到 OpenAI 兼容路径: {exc}"
            )

    # ------------------------------------------------------------------ utils
    def _load_prompt(self, path: Path, **kwargs) -> str:
        if not path.exists():
            return f"Prompt file not found: {path}"
        content = path.read_text(encoding="utf-8")
        for k, v in kwargs.items():
            content = content.replace(f"{{{k}}}", str(v))
        return content

    def _sanitize_filename(self, filename: str, replacement: str = "-") -> str:
        pattern = r'[<>:"/\\|?*\x00-\x1F]'
        sanitized = re.sub(pattern, replacement, filename)
        return sanitized.strip(" .")

    def _resolve_task_doc_paths(self, docs: list[str] | None) -> list[Path]:
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
        if not docs_abs:
            return ""
        lines = [
            "### 当前任务参考文件（仅文件名）",
            "调用 `load_memory` 时，`txt_path` **只填下方列表里的文件名**（如 `TEA_xxx.txt`），"
            "系统会自动映射到 workspace 目录下的真实文件。禁止填目录、禁止拼绝对路径、"
            "禁止使用列表以外的文件名。",
        ]
        for p in docs_abs:
            lines.append(f"- {p.name}")
        return "\n".join(lines)

    def _resolve_memory_txt(self, txt_path: str) -> tuple[Path | None, str | None]:
        """把模型传入的 txt_path 解析为 workspace 下的真实文件。

        仅接受"文件名"或"workspace/<文件名>"；绝对路径会被剥成文件名再解析，
        防止模型臆造目录。返回 (resolved_path, error)。
        """
        if not txt_path or not txt_path.strip():
            return None, "txt_path 为空"
        name = Path(txt_path.strip()).name  # 强制只取文件名
        workspace = self.project_root / "workspace"
        candidate = workspace / name
        if candidate.exists() and candidate.is_file():
            return candidate, None
        # 容错：列出 workspace 里现有的 txt 提示模型
        available = sorted(p.name for p in workspace.glob("*.txt"))
        hint = "、".join(available[:20]) if available else "(空)"
        return None, (
            f"未在 workspace/ 下找到 {name!r}；请改填任务参考文件列表里的文件名。"
            f" 现有 txt（最多 20 个）: {hint}"
        )

    # ---------------------------------------------------------------- prompts
    def _build_react_prompt(
        self,
        task: TaskRecord,
        io_info: dict[str, Any],
        circuit_list: list[Any],
        selected_circuit: str | None,
    ) -> str:
        prompt_path = self.prompt_dir / "verification" / "blueprint.txt"
        prompt = self._load_prompt(
            prompt_path,
            goal=task.analysis_raw,
            task_name=task.task_name,
            target_subcircuit=selected_circuit or task.target_subcircuit or "未指定",
            circuit_list=json.dumps(circuit_list, ensure_ascii=False),
            io_input=json.dumps(io_info.get("inputs", []), ensure_ascii=False),
            io_output=json.dumps(io_info.get("outputs", []), ensure_ascii=False),
            io_all=json.dumps(io_info.get("all_labeled", []), ensure_ascii=False),
        )
        docs_block = self._build_task_docs_prompt_block(
            self._resolve_task_doc_paths(task.task_instruction_docs)
        )
        if docs_block:
            prompt += "\n\n" + docs_block
        return prompt

    # ----------------------------------------------------------------- runner
    async def run(self, task: TaskRecord, circ_path: Path) -> TaskRecord:
        if self.cache:
            cached = self.cache.get_task_if_done(task)
            if cached:
                ok, reason = await self._validate_cached_task(cached)
                if ok:
                    print(f"[Verification] 缓存通过目标校验: {cached.task_name}")
                    return cached
                print(
                    f"[Verification] 缓存未通过目标校验（{reason}），"
                    f"将清理并重跑: {cached.task_name}"
                )
                self._invalidate_task_cache(task)

        if not self.emulator:
            self.emulator = LogisimEmulator(self.config, self.client)

        save_dir = Path("output") / "实验报告.assets"
        save_dir.mkdir(parents=True, exist_ok=True)
        filtered_taskname = self._sanitize_filename(task.task_name)
        output_path = save_dir / f"{filtered_taskname}.png"

        last_failure_reason: str = ""
        max_attempts = self._MAX_GOAL_RETRIES + 1
        for attempt in range(max_attempts):
            attempt_tag = f"[Verification] 第 {attempt + 1}/{max_attempts} 次尝试"
            print(
                f"{attempt_tag} — 任务: {task.task_name}，目标子电路: {task.target_subcircuit}"
            )
            # 每次尝试开始前，清理残留产物，保证截图/分析都被覆盖
            self._reset_task_outputs(task, output_path)

            print(f"[Verification] 正在连接并加载电路: {circ_path}")
            success = await self.emulator.launch_and_initialize(str(circ_path))
            if not success:
                task.status = "failed"
                task.analysis_raw = "无法连接或加载电路，请确保后端服务 (ws://localhost:9924/ws) 正在运行。"
                return task

            verdict = await self._run_react_loop(task, circ_path)

            if verdict is None or not verdict.get("goal_reached"):
                reason = (verdict or {}).get("reason") or "验证未收到模型最终判定"
                last_failure_reason = f"ReAct 未达目标: {reason}"
                print(f"[Verification] {last_failure_reason}")
                if attempt < max_attempts - 1:
                    continue
                task.status = "failed"
                task.analysis_raw = last_failure_reason
                raise RuntimeError(f"Verification failed: {reason}")

            # 最终抓图（覆盖旧图）
            print("[Verification] 正在保存最终状态截图...")
            snap_resp = await self.emulator.send_command(
                "get_screenshot", width=1920, height=1080
            )
            if not (
                isinstance(snap_resp, dict)
                and snap_resp.get("status") == "ok"
                and "binary" in snap_resp
            ):
                err_msg = (
                    snap_resp.get("message")
                    if isinstance(snap_resp, dict)
                    else str(snap_resp)
                )
                last_failure_reason = f"获取最终状态截图失败: {err_msg}"
                print(f"[Verification] {last_failure_reason}")
                if attempt < max_attempts - 1:
                    continue
                raise RuntimeError(last_failure_reason)

            output_path.unlink(missing_ok=True)
            output_path.write_bytes(snap_resp["binary"])
            task.assets = [str(output_path)]
            print(f"[Verification] 最终状态截图已保存: {output_path}")

            # 生成包裹分析
            explanation = await self._generate_wrapped_analysis_with_retry(
                image=PIL.Image.open(output_path),
                task_desc=verdict.get("reason") or task.analysis_raw,
                max_retries=2,
            )

            # 目标校验：截图 + 分析 是否真正达成该切片实验目标
            goal_met, judge_reason = await self._verify_goal_met(
                image_path=output_path,
                analysis=explanation,
                task=task,
                verdict=verdict,
            )
            if goal_met:
                task.status = "finished"
                task.analysis_raw = explanation
                print(f"[Verification] 目标校验通过: {judge_reason}")
                if self.cache:
                    self.cache.save_task(task)
                return task

            last_failure_reason = f"目标校验未通过: {judge_reason}"
            print(f"[Verification] {last_failure_reason}")
            if attempt < max_attempts - 1:
                print(
                    f"[Verification] 将删除本次截图与分析，重跑 ReAct（剩余 {max_attempts - attempt - 1} 次）。"
                )
                continue

            # 最后一次仍失败
            task.status = "failed"
            task.analysis_raw = f"{last_failure_reason}\n\n最后一次生成的分析（未通过）：\n{explanation}"
            raise RuntimeError(f"Verification goal-check failed: {judge_reason}")

        # 理论不可达
        task.status = "failed"
        task.analysis_raw = last_failure_reason or "验证重试全部失败"
        raise RuntimeError(task.analysis_raw)

    def _reset_task_outputs(self, task: TaskRecord, output_path: Path) -> None:
        """清理上一次尝试留下的截图与缓存，保证新一次覆盖。"""
        try:
            output_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"[Verification] 删除旧截图失败（忽略）: {e}")
        task.assets = []
        task.analysis_raw = ""
        task.status = "pending"
        # 清缓存中旧记录，避免下游复用失败残留
        if self.cache:
            try:
                cache_file = self.cache._task_path(task.task_id)
                if cache_file.exists():
                    cache_file.unlink()
            except Exception as e:
                print(f"[Verification] 删除旧缓存失败（忽略）: {e}")

    def _invalidate_task_cache(self, task: TaskRecord) -> None:
        """只删缓存 JSON，不碰截图（供命中缓存后想重跑时使用）。"""
        if not self.cache:
            return
        try:
            cache_file = self.cache._task_path(task.task_id)
            if cache_file.exists():
                cache_file.unlink()
                print(f"[Verification] 已失效旧缓存: {task.task_id}")
        except Exception as e:
            print(f"[Verification] 失效缓存失败（忽略）: {e}")

    async def _validate_cached_task(self, cached: TaskRecord) -> tuple[bool, str]:
        """对缓存命中的任务重跑一次目标校验（截图 + 分析）。"""
        if cached.status != "finished":
            return False, f"缓存状态非 finished: {cached.status}"
        if not cached.assets:
            return False, "缓存中缺少截图路径"
        img_path = Path(cached.assets[0])
        if not img_path.exists():
            return False, f"缓存截图文件不存在: {img_path}"
        if not (cached.analysis_raw or "").strip():
            return False, "缓存中分析为空"
        ok, reason = await self._verify_goal_met(
            image_path=img_path,
            analysis=cached.analysis_raw,
            task=cached,
            verdict={"reason": "cached-replay"},
        )
        return ok, reason

    def _robust_parse_goal_json(self, text: str) -> dict[str, Any] | None:
        """对模型返回做尽可能宽容的 JSON 抽取。

        处理常见脏数据：
          - ```json ... ``` / ``` ... ``` 围栏
          - 前后解释性文字
          - 列表包裹 [{...}]
          - 单/双 smart quote 残留（仅 key/value 边界）
        解析成功但非 dict → 返回 None 让上层兜底。
        """
        if not text:
            return None
        s = text.strip()

        def _try_loads(candidate: str) -> Any:
            try:
                return json.loads(candidate)
            except Exception:
                extracted = self._extract_json(candidate)
                if extracted:
                    try:
                        return json.loads(extracted)
                    except Exception:
                        return None
                return None

        # 1) 最常见：已经是干净 JSON，直接解析，避免 smart-quote 归一
        #    破坏字符串值内部的中文引号（如 "xxx"）。
        parsed: Any = _try_loads(s)

        # 2) 次常见：带 ``` / ```json 围栏
        if parsed is None and s.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z]*\s*", "", s)
            stripped = re.sub(r"\s*```\s*$", "", stripped)
            parsed = _try_loads(stripped)
            if parsed is not None:
                s = stripped

        # 3) 再不行再做 smart-quote 归一（可能损伤正文但作为兜底）
        if parsed is None:
            normalized = (
                s.replace("\u201c", '"')
                .replace("\u201d", '"')
                .replace("\u2018", "'")
                .replace("\u2019", "'")
            )
            parsed = _try_loads(normalized)

        if parsed is None:
            return None
        if isinstance(parsed, list) and parsed:
            parsed = parsed[0]
        if not isinstance(parsed, dict):
            return None
        return parsed

    async def _verify_goal_met(
        self,
        image_path: Path,
        analysis: str,
        task: TaskRecord,
        verdict: dict[str, Any],
    ) -> tuple[bool, str]:
        """由 Pro 模型看截图+分析，判断是否真正达成实验目标。返回 (ok, reason)。

        策略：
          - 校验器自身失败（API 抛错 / 空响应 / JSON 解析失败 / 结构异常）
            → 追加更多上下文（截图、任务参考 txt 文本、TaskRecord 目的）
              再喂一次，最多 `max_retries` 次；
          - 直到成功解析出 JSON。超过重试上限仍失败才保守视为通过
            （不冤枉已跑出的合格结果）。
        """

        task_goal = (
            task.analysis_raw or task.section_text or task.task_name or ""
        ).strip()

        # 预读任务参考 txt（如 RAM/ROM 测试数据），便于校验器核对
        doc_paths = self._resolve_task_doc_paths(task.task_instruction_docs)
        doc_blocks: list[str] = []
        for p in doc_paths:
            try:
                if p.suffix.lower() in {".txt", ".md", ".json"}:
                    body = p.read_text(encoding="utf-8", errors="ignore")
                    if len(body) > 4000:
                        body = body[:4000] + "\n...[truncated]"
                    doc_blocks.append(f"--- {p.name} ---\n{body}")
            except Exception as e:
                doc_blocks.append(f"--- {p.name} ---\n[读取失败: {e}]")
        docs_text = "\n\n".join(doc_blocks) if doc_blocks else "（无）"

        base_prompt = (
            "你是数字电路实验验收员。请结合下方提供的【任务目标】【任务参考文件（txt 等）】"
            "【ReAct 自判理由】【生成的分析正文】以及附带的【截图】，判断是否真实、"
            "可验证地达成了该【切片目标】——**只对本切片目标负责，不要套用通用"
            '"输出必须有效数值"之类的额外标准**。\n\n'
            "判断原则（按场景灵活裁量，不是硬性全满足）：\n"
            "  1. **语义一致性**：截图、分析正文、参考文件三者相互印证，描述的现象在截图里能找到对应证据。\n"
            "  2. **目标契合**：实验现象确实对应切片目标所要验证的逻辑。\n"
            '     - 例如目标是"验证 SEL=1 时 RAM 输出被隔离/不工作"，那么 outputPin 显示\n'
            "       `xxxxxxxx` / 高阻 / 浮空 **就是预期现象**，分析把它解释为三态隔离是合理的，应判 true。\n"
            '     - 例如目标是"验证 RAM 读出某个写入值"，此时 outputPin 仍为 `xxxxxxxx`\n'
            "       才算失败。\n"
            "  3. **不要一刀切**：`xxxxxxxx` / `zzzz` / 高阻 / 浮空 / 全 x 本身不是残缺描述，\n"
            '     要看切片目标要求的是"看到数值"还是"看到隔离/未驱动"。同理"拉高""未知""浮空"\n'
            "     等词在对应场景下都是合法描述。\n"
            "  4. **真正的不通过**：分析与截图矛盾、分析声称读到具体值但截图并没有、或完全答非所问等。\n\n"
            "给出判定时在 reason 里说清：切片目标是什么，截图里的证据是什么，为什么与目标契合/不契合。\n\n"
            f"【实验名称】{task.task_name}\n"
            f"【任务类型】{task.task_type}\n"
            f"【目标子电路】{task.target_subcircuit or '未指定'}\n"
            f"【任务目标 / 切片目的】\n{task_goal}\n\n"
            f"【任务参考文件内容】\n{docs_text}\n\n"
            f"【ReAct 自判理由】{verdict.get('reason', '')}\n"
            f"【生成的分析正文】\n{analysis}\n\n"
            "必须严格输出如下 JSON（且只输出 JSON，不要其它文字）：\n"
            '{"goal_met": true|false, "reason": "..."}'
        )

        max_retries = 3
        last_error = ""
        last_raw = ""
        for attempt in range(max_retries):
            if attempt == 0:
                prompt = base_prompt
            else:
                prompt = (
                    "你上一次的回复无法被解析为合法 JSON。请务必只输出一个纯 JSON 对象，"
                    '结构为 {"goal_met": true|false, "reason": "..."}，'
                    "不要 ```json``` 包裹，不要任何前言后语。\n\n"
                    f"上一次错误：{last_error}\n"
                    f"上一次返回（截断 500 字）：\n{last_raw[:500]}\n\n" + base_prompt
                )
            try:
                response = await generate_content_with_tools(
                    self.client,
                    model=self.config.gemini.model_pro,
                    contents=[PIL.Image.open(image_path), prompt],
                    config={"response_mime_type": "application/json"},
                )
                last_raw = (response.text or "").strip()
            except Exception as e:
                last_error = f"API 异常: {e}"
                print(
                    f"[Verification] 目标校验第 {attempt + 1}/{max_retries} 次调用失败，准备重试: {e}"
                )
                continue

            parsed = self._robust_parse_goal_json(last_raw)

            if parsed is None:
                last_error = "JSON 解析失败"
                print(
                    f"[Verification] 目标校验第 {attempt + 1}/{max_retries} 次 JSON 解析失败，"
                    f"raw[:200]={last_raw[:200]!r}"
                )
                continue

            if "goal_met" not in parsed:
                last_error = f"结构异常（缺少 goal_met）: {parsed!r}"
                print(
                    f"[Verification] 目标校验第 {attempt + 1}/{max_retries} 次结构异常: {parsed!r}"
                )
                continue

            return (
                bool(parsed.get("goal_met")),
                str(parsed.get("reason", "")).strip(),
            )

        raise RuntimeError(
            f"[Verification] 目标校验连续 {max_retries} 次均失败（{last_error}），"
            f"原始最后输出前 500 字：\n{last_raw[:500]}"
        )

    # ----------------------------------------------------------- ReAct driver
    async def _run_react_loop(
        self, task: TaskRecord, circ_path: Path
    ) -> dict[str, Any] | None:
        """驱动 LLM 在 ReAct 模式下逐步操作电路，直至其主动 report_verdict。"""

        # 1. 子电路识别（最多重试 _SWITCH_RETRIES 次，配合截图反馈）
        circuits_resp = await self.emulator.send_command("get_circuits")
        circuit_list = (
            circuits_resp.get("payload", []) if isinstance(circuits_resp, dict) else []
        )
        selected_circuit: str | None = None
        last_screenshot: bytes | None = None
        for switch_attempt in range(self._SWITCH_RETRIES + 1):
            selected_circuit = await self._identify_and_switch_circuit(
                task, circuit_list, screenshot_bytes=last_screenshot
            )
            if selected_circuit:
                break
            # 抓当前截图，让 Flash 下一轮结合视觉重选
            snap = await self.emulator.send_command(
                "get_screenshot", width=1280, height=720
            )
            if (
                isinstance(snap, dict)
                and snap.get("status") == "ok"
                and "binary" in snap
            ):
                last_screenshot = snap["binary"]
            print(
                f"[Verification] 子电路切换失败，准备第 {switch_attempt + 2}/"
                f"{self._SWITCH_RETRIES + 1} 次重试..."
            )
        if not selected_circuit:
            print(
                "[Warning] 无法可靠地切换到目标子电路，后续 ReAct 仍会尝试，但结果未必可靠。"
            )

        # 2. 初始上下文：引脚信息 + 截图
        io_resp = await self.emulator.send_command("get_io")
        io_info = io_resp.get("payload", {}) if isinstance(io_resp, dict) else {}
        snap = await self.emulator.send_command(
            "get_screenshot", width=1280, height=720
        )
        screenshot_bytes: bytes | None = None
        if isinstance(snap, dict) and snap.get("status") == "ok" and "binary" in snap:
            screenshot_bytes = snap["binary"]

        base_prompt = self._build_react_prompt(
            task, io_info, circuit_list, selected_circuit
        )

        if screenshot_bytes:
            contents: Any = [
                PIL.Image.open(io.BytesIO(screenshot_bytes)),
                base_prompt
                + "\n\n### 参考截图\n上图是目标子电路的当前视图，"
                + "用于辅助定位组件；所有引脚值仍以工具返回为准。",
            ]
        else:
            contents = base_prompt

        tools = self._build_react_tools()
        # 把 AFC 预算抬高到 _MAX_TOOL_ROUNDS，保证足够步数逐步推进。
        afc_budget = max(1, self._MAX_TOOL_ROUNDS)
        continuation_rounds = max(1, (self._MAX_TOOL_ROUNDS + 63) // 64)

        for attempt in range(self._OUTER_RETRIES + 1):
            self._verdict = None
            self._tool_trace = []
            self._tool_call_count = 0

            print(
                f"[Verification] 启动 ReAct 会话 (第 {attempt + 1}/"
                f"{self._OUTER_RETRIES + 1} 轮)，最大工具调用 {self._MAX_TOOL_ROUNDS} 次。"
            )

            try:
                if self._genai_client is not None:
                    response = await generate_react_native(
                        self._genai_client,
                        model=self.config.gemini.model_pro,
                        contents=contents,
                        tools=tools,
                        max_rounds=self._MAX_TOOL_ROUNDS,
                        is_done=lambda: self._verdict is not None,
                    )
                else:
                    response = await generate_content_with_tools(
                        self.client,
                        model=self.config.gemini.model_pro,
                        contents=contents,
                        extra_tools=tools,
                        max_continuation_rounds=continuation_rounds,
                        config={
                            "automatic_function_calling": {
                                "maximum_remote_calls": afc_budget
                            }
                        },
                    )
            except Exception as e:
                print(f"[Verification] ReAct 会话异常: {e}")
                contents = self._build_react_retry_prompt(
                    reason=f"上轮调用异常: {e}",
                    last_text="",
                    io_info=io_info,
                )
                continue

            if self._verdict is not None:
                print(
                    f"[Verification] ReAct 会话结束，工具调用 {self._tool_call_count} 次。"
                )
                return self._verdict

            # 兜底：尝试从最终文本解析判定
            parsed = self._parse_text_verdict(response.text or "")
            if parsed is not None:
                return parsed

            print("[Verification] 本轮未获得 report_verdict，准备注入追问。")
            contents = self._build_react_retry_prompt(
                reason="你尚未调用 report_verdict，也未输出 JSON 判定",
                last_text=response.text or "",
                io_info=io_info,
            )

        return self._verdict

    def _build_react_retry_prompt(
        self,
        reason: str,
        last_text: str,
        io_info: dict[str, Any],
    ) -> str:
        tail = (last_text or "").strip()
        if len(tail) > 800:
            tail = tail[:800] + "..."
        trace_tail = self._tool_trace[-10:]
        trace_json = json.dumps(trace_tail, ensure_ascii=False, indent=2)
        return (
            f"问题: {reason}。\n"
            + "请继续以 ReAct 模式推进：每次只执行一个工具调用，"
            + "读取真实 Observation 后再决定下一步；"
            + "一旦已确认目标达成或多次尝试仍失败，必须调用 "
            + "report_verdict(goal_reached, reason) 收尾，不要空转。\n\n"
            + "### 最新 IO 快照\n"
            + json.dumps(io_info, ensure_ascii=False)[:1500]
            + "\n\n### 最近工具调用轨迹\n"
            + trace_json
            + "\n\n### 上一轮最终文本（若非空，说明你忘了调用 report_verdict）\n"
            + tail
        )

    # ----------------------------------------------------------- ReAct tools
    def _build_react_tools(self) -> list[Any]:
        """构造绑定到当前 emulator 的 ReAct 工具集。

        所有工具都是 async 且捕获异常，保证 Observation 永远是字符串，
        避免 retry_llm_call 把单个硬件错误升格为整轮失败。
        """
        agent = self

        async def _send(action: str, **kwargs: Any) -> dict[str, Any]:
            emu = agent.emulator
            if emu is None:
                return {"status": "error", "message": "emulator_not_initialized"}
            return await emu.send_command(action, **kwargs)

        def _record(
            tool: str, args: dict[str, Any], observation: dict[str, Any]
        ) -> str:
            agent._tool_call_count += 1
            obs_copy = {
                k: (v if k != "binary" else "<binary omitted>")
                for k, v in observation.items()
            }
            agent._tool_trace.append(
                {
                    "n": agent._tool_call_count,
                    "tool": tool,
                    "args": args,
                    "observation": obs_copy,
                }
            )
            # 软警告：只在特定里程碑次数触发一次，避免反复刷屏影响判断
            n = agent._tool_call_count
            warn = None
            milestone_set = {
                agent._TOOL_WARN_THRESHOLD,
                agent._TOOL_HARD_LIMIT,
            }
            if tool != "report_verdict" and n in milestone_set:
                if n >= agent._TOOL_HARD_LIMIT:
                    warn = (
                        f"[提示] 已调用工具 {n} 次；如判断已无进展可直接 "
                        "report_verdict 收尾（goal_reached 按实际判断）。"
                    )
                else:
                    warn = (
                        f"[提示] 已调用工具 {n} 次；若已完成验证请及时 "
                        "report_verdict 收尾。"
                    )
            if warn:
                obs_copy = {**obs_copy, "__system_warning__": warn}
            return json.dumps(obs_copy, ensure_ascii=False)

        def _safe(observation: Any) -> dict[str, Any]:
            if isinstance(observation, dict):
                return observation
            return {"status": "error", "message": f"非字典响应: {observation!r}"}

        async def get_io() -> str:
            """刷新并返回当前子电路的 inputs / outputs / all_labeled 标签列表。调用时机：开场摸底、任何标签相关错误之后、set_value 报错后重新核对。"""
            try:
                resp = _safe(await _send("get_io"))
                payload = resp.get("payload", {}) if resp.get("status") == "ok" else {}
                payload = payload if isinstance(payload, dict) else {}
                obs = {
                    "ok": resp.get("status") == "ok",
                    "inputs": payload.get("inputs", []),
                    "outputs": payload.get("outputs", []),
                    "all_labeled": payload.get("all_labeled", []),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("get_io", {}, obs)
            except Exception as e:
                return _record("get_io", {}, {"ok": False, "error": str(e)})

        async def get_value(target: str) -> str:
            """读取指定标签的当前值（十进制字符串）。target 必须逐字来自 all_labeled。用于观察电路状态而不改变它。"""
            try:
                resp = _safe(await _send("get_value", target=target))
                if resp.get("status") == "ok":
                    obs = {
                        "ok": True,
                        "target": target,
                        "value": resp.get("payload"),
                    }
                else:
                    obs = {
                        "ok": False,
                        "target": target,
                        "error": resp.get("message"),
                    }
                return _record("get_value", {"target": target}, obs)
            except Exception as e:
                return _record(
                    "get_value",
                    {"target": target},
                    {"ok": False, "error": str(e)},
                )

        async def set_value(target: str, value: str) -> str:
            """设置输入引脚的值。target 必须来自 inputs 列表；value 为十进制字符串（如 "0" / "5" / "12345678"）。"""
            try:
                resp = _safe(await _send("set_value", target=target, value=value))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "target": target,
                    "value": value,
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("set_value", {"target": target, "value": value}, obs)
            except Exception as e:
                return _record(
                    "set_value",
                    {"target": target, "value": value},
                    {"ok": False, "error": str(e)},
                )

        async def check_value(target: str, expected: str) -> str:
            """断言 target 当前值等于 expected。target 来自 all_labeled，expected 为十进制或十六进制字符串。返回 {ok, matched, actual}。"""
            try:
                resp = _safe(
                    await _send("check_value", target=target, expected=expected)
                )
                ok = resp.get("status") == "ok"
                payload = resp.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                obs = {
                    "ok": ok,
                    "target": target,
                    "expected": expected,
                    "matched": bool(payload.get("matched")) if payload else ok,
                    "actual": payload.get("actual"),
                    "error": resp.get("message") if not ok else None,
                }
                return _record(
                    "check_value",
                    {"target": target, "expected": expected},
                    obs,
                )
            except Exception as e:
                return _record(
                    "check_value",
                    {"target": target, "expected": expected},
                    {"ok": False, "error": str(e)},
                )

        async def run_tick(tick_count: int) -> str:
            """推进电路 tick_count 个时钟周期。典型取值 1~10；不要用来替代 tick_until。"""
            try:
                resp = _safe(await _send("run_tick", tick_count=int(tick_count)))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "tick_count": int(tick_count),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("run_tick", {"tick_count": int(tick_count)}, obs)
            except Exception as e:
                return _record(
                    "run_tick",
                    {"tick_count": int(tick_count)},
                    {"ok": False, "error": str(e)},
                )

        async def tick_until(
            target: str,
            expected: str,
            max_ticks: int = 100000,
            clock: str = "",
        ) -> str:
            """持续 tick 直到 target 值等于 expected 或达到 max_ticks。clock 可选，若指定必须是 inputs 中的时钟引脚。"""
            kwargs: dict[str, Any] = {
                "target": target,
                "expected": expected,
                "max": int(max_ticks),
            }
            if clock:
                kwargs["clock"] = clock
            try:
                resp = _safe(await _send("tick_until", **kwargs))
                ok = resp.get("status") == "ok"
                obs = {
                    "ok": ok,
                    "target": target,
                    "expected": expected,
                    "payload": resp.get("payload"),
                    "error": resp.get("message") if not ok else None,
                }
                return _record("tick_until", kwargs, obs)
            except Exception as e:
                return _record(
                    "tick_until",
                    kwargs,
                    {"ok": False, "error": str(e)},
                )

        async def run_until_stable_then_tick(
            target: str,
            timeout_second: int = 10,
            k: int = 0,
            stable_samples: int = 5,
            poll_ms: int = 20,
        ) -> str:
            """等待 target 信号稳定（连续 stable_samples 次采样一致）后再 tick k 次。用于异步/组合回路稳态。"""
            kwargs = {
                "target": target,
                "timeout_second": int(timeout_second),
                "k": int(k),
                "stable_samples": int(stable_samples),
                "poll_ms": int(poll_ms),
            }
            try:
                resp = _safe(await _send("run_until_stable_then_tick", **kwargs))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "payload": resp.get("payload"),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("run_until_stable_then_tick", kwargs, obs)
            except Exception as e:
                return _record(
                    "run_until_stable_then_tick",
                    kwargs,
                    {"ok": False, "error": str(e)},
                )

        async def load_memory(target: str, txt_path: str) -> str:
            """为存储器类组件（RAM/ROM）从 txt 文件加载数据。

            txt_path **只填文件名**（例如 `TEA_xxx.txt`），不要给目录/绝对路径，
            系统会自动到 workspace/ 下查找；若文件名不在任务参考文件列表里会直接报错。
            """
            resolved, err = agent._resolve_memory_txt(txt_path)
            if err:
                return _record(
                    "load_memory",
                    {"target": target, "txt_path": txt_path},
                    {"ok": False, "target": target, "txt_path": txt_path, "error": err},
                )
            try:
                resp = _safe(
                    await _send("load_memory", target=target, txt_path=str(resolved))
                )
                obs = {
                    "ok": resp.get("status") == "ok",
                    "target": target,
                    "txt_path": str(resolved),
                    "input_txt_path": txt_path,
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record(
                    "load_memory",
                    {"target": target, "txt_path": txt_path},
                    obs,
                )
            except Exception as e:
                return _record(
                    "load_memory",
                    {"target": target, "txt_path": txt_path},
                    {"ok": False, "error": str(e)},
                )

        async def list_components(
            factory_name: str = "",
            label: str = "",
            is_memory: bool | None = None,
            addr_bits: int | None = None,
            data_bits: int | None = None,
        ) -> str:
            """列出当前子电路组件清单，支持按类型/标签/位宽筛选。"""
            try:
                kwargs: dict[str, Any] = {}
                if factory_name:
                    kwargs["factory_name"] = factory_name
                if label:
                    kwargs["label"] = label
                if is_memory is not None:
                    kwargs["is_memory"] = bool(is_memory)
                if addr_bits is not None:
                    kwargs["addr_bits"] = int(addr_bits)
                if data_bits is not None:
                    kwargs["data_bits"] = int(data_bits)
                resp = _safe(await _send("list_components", **kwargs))
                payload = resp.get("payload")
                payload = payload if isinstance(payload, list) else []
                obs = {
                    "ok": resp.get("status") == "ok",
                    "filters": kwargs,
                    "components": payload,
                    "count": len(payload),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("list_components", kwargs, obs)
            except Exception as e:
                return _record(
                    "list_components",
                    {
                        "factory_name": factory_name,
                        "label": label,
                        "is_memory": is_memory,
                        "addr_bits": addr_bits,
                        "data_bits": data_bits,
                    },
                    {"ok": False, "error": str(e)},
                )

        async def resolve_component(
            target: str = "",
            factory_name: str = "",
            label: str = "",
            is_memory: bool | None = None,
            addr_bits: int | None = None,
            data_bits: int | None = None,
            index: int | None = None,
            sort: str = "",
        ) -> str:
            """把标签/筛选条件解析为稳定 comp_id；歧义时返回候选与提示。"""
            try:
                kwargs: dict[str, Any] = {}
                if target:
                    kwargs["target"] = target
                if factory_name:
                    kwargs["factory_name"] = factory_name
                if label:
                    kwargs["label"] = label
                if is_memory is not None:
                    kwargs["is_memory"] = bool(is_memory)
                if addr_bits is not None:
                    kwargs["addr_bits"] = int(addr_bits)
                if data_bits is not None:
                    kwargs["data_bits"] = int(data_bits)
                if index is not None:
                    kwargs["index"] = int(index)
                if sort:
                    kwargs["sort"] = sort
                resp = _safe(await _send("resolve_component", **kwargs))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "query": kwargs,
                    "payload": resp.get("payload"),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("resolve_component", kwargs, obs)
            except Exception as e:
                return _record(
                    "resolve_component",
                    {
                        "target": target,
                        "factory_name": factory_name,
                        "label": label,
                        "is_memory": is_memory,
                        "addr_bits": addr_bits,
                        "data_bits": data_bits,
                        "index": index,
                        "sort": sort,
                    },
                    {"ok": False, "error": str(e)},
                )

        async def get_component_info_by_id(comp_id: str) -> str:
            """按 comp_id 读取组件详细信息（类型、位宽、端口、存储参数等）。"""
            cid = str(comp_id or "").strip()
            try:
                resp = _safe(await _send("get_component_info_by_id", comp_id=cid))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "comp_id": cid,
                    "payload": resp.get("payload"),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("get_component_info_by_id", {"comp_id": cid}, obs)
            except Exception as e:
                return _record(
                    "get_component_info_by_id",
                    {"comp_id": cid},
                    {"ok": False, "error": str(e)},
                )

        async def describe_component(comp_id: str) -> str:
            """按 comp_id 获取更易读的组件描述，便于 LLM 快速理解组件作用。"""
            cid = str(comp_id or "").strip()
            try:
                resp = _safe(await _send("describe_component", comp_id=cid))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "comp_id": cid,
                    "payload": resp.get("payload"),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("describe_component", {"comp_id": cid}, obs)
            except Exception as e:
                return _record(
                    "describe_component",
                    {"comp_id": cid},
                    {"ok": False, "error": str(e)},
                )

        async def load_memory_by_id(comp_id: str, txt_path: str) -> str:
            """按 comp_id 为存储器加载 txt 数据。txt_path 只填文件名。"""
            cid = str(comp_id or "").strip()
            resolved, err = agent._resolve_memory_txt(txt_path)
            if err:
                return _record(
                    "load_memory_by_id",
                    {"comp_id": cid, "txt_path": txt_path},
                    {
                        "ok": False,
                        "comp_id": cid,
                        "txt_path": txt_path,
                        "error": err,
                    },
                )
            try:
                resp = _safe(
                    await _send(
                        "load_memory_by_id",
                        comp_id=cid,
                        txt_path=str(resolved),
                    )
                )
                obs = {
                    "ok": resp.get("status") == "ok",
                    "comp_id": cid,
                    "txt_path": str(resolved),
                    "input_txt_path": txt_path,
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record(
                    "load_memory_by_id",
                    {"comp_id": cid, "txt_path": txt_path},
                    obs,
                )
            except Exception as e:
                return _record(
                    "load_memory_by_id",
                    {"comp_id": cid, "txt_path": txt_path},
                    {"ok": False, "error": str(e)},
                )

        async def get_component_info(target: str) -> str:
            """读取组件详细信息（位宽、地址空间、端口等）。用于排查标签歧义或确认位宽。"""
            try:
                resp = _safe(await _send("get_component_info", target=target))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "target": target,
                    "payload": resp.get("payload"),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("get_component_info", {"target": target}, obs)
            except Exception as e:
                return _record(
                    "get_component_info",
                    {"target": target},
                    {"ok": False, "error": str(e)},
                )

        async def get_circuits() -> str:
            """列出当前工程中所有可用子电路名称。仅在需要重新挑选子电路时调用。"""
            try:
                resp = _safe(await _send("get_circuits"))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "circuits": resp.get("payload", []),
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("get_circuits", {}, obs)
            except Exception as e:
                return _record("get_circuits", {}, {"ok": False, "error": str(e)})

        async def switch_circuit(name: str) -> str:
            """切换到指定子电路。调用后原有 IO 标签可能失效，必须立刻 get_io 刷新。"""
            try:
                resp = _safe(await _send("switch_circuit", name=name))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "name": name,
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("switch_circuit", {"name": name}, obs)
            except Exception as e:
                return _record(
                    "switch_circuit", {"name": name}, {"ok": False, "error": str(e)}
                )

        async def load_circuit(path: str) -> str:
            """重新加载 .circ 工程文件。一般不需要调用，除非当前工程损坏或需要切换工程。"""
            try:
                resp = _safe(await _send("load_circuit", path=path))
                obs = {
                    "ok": resp.get("status") == "ok",
                    "path": path,
                    "error": (
                        resp.get("message") if resp.get("status") != "ok" else None
                    ),
                }
                return _record("load_circuit", {"path": path}, obs)
            except Exception as e:
                return _record(
                    "load_circuit", {"path": path}, {"ok": False, "error": str(e)}
                )

        async def get_screenshot(width: int = 1280, height: int = 720) -> Any:
            """抓取当前子电路渲染截图，并把图像本身反馈给模型（作为下一轮 user 消息中的 image 部分）。
            仅在关键节点使用（例如确认切换到了正确的子电路、调试时需要视觉定位组件），不要频繁调用。
            """
            try:
                resp = _safe(
                    await _send("get_screenshot", width=int(width), height=int(height))
                )
                ok = resp.get("status") == "ok" and "binary" in resp
                binary = resp.get("binary") if ok else None
                size = len(binary) if isinstance(binary, (bytes, bytearray)) else 0
                obs: dict[str, Any] = {
                    "ok": ok,
                    "width": int(width),
                    "height": int(height),
                    "size_bytes": size,
                    "error": resp.get("message") if not ok else None,
                }
                # 记录 trace（不带二进制）
                agent._tool_call_count += 1
                agent._tool_trace.append(
                    {
                        "tool": "get_screenshot",
                        "args": {"width": int(width), "height": int(height)},
                        "observation": obs,
                    }
                )
                if ok and isinstance(binary, (bytes, bytearray)):
                    # 返回 dict 给 ReAct 循环；__image__ 字段会被框架抽取
                    # 成独立的 user-role image Part，不会进入 FunctionResponse。
                    return {
                        **obs,
                        "__image__": bytes(binary),
                        "__image_mime__": "image/png",
                    }
                return obs
            except Exception as e:
                return _record(
                    "get_screenshot",
                    {"width": int(width), "height": int(height)},
                    {"ok": False, "error": str(e)},
                )

        async def report_verdict(goal_reached: bool, reason: str) -> str:
            """提交最终验证判定。goal_reached=True 表示目标达成；False 表示失败/无法达成。调用此工具后请停止输出，不要再调用任何工具。"""
            agent._verdict = {
                "goal_reached": bool(goal_reached),
                "reason": str(reason or ""),
            }
            observation = {"ok": True, "recorded": True}
            return _record(
                "report_verdict",
                {
                    "goal_reached": bool(goal_reached),
                    "reason": str(reason or ""),
                },
                observation,
            )

        return [
            get_io,
            get_value,
            set_value,
            check_value,
            run_tick,
            tick_until,
            run_until_stable_then_tick,
            load_memory,
            load_memory_by_id,
            get_component_info,
            list_components,
            resolve_component,
            get_component_info_by_id,
            describe_component,
            get_circuits,
            switch_circuit,
            load_circuit,
            get_screenshot,
            report_verdict,
        ]

    def _parse_text_verdict(self, text: str) -> dict[str, Any] | None:
        if not text:
            return None
        extracted = self._extract_json(text)
        if not extracted:
            return None
        try:
            data = json.loads(extracted)
        except Exception:
            return None
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return None
        if "goal_reached" not in data:
            return None
        return {
            "goal_reached": bool(data.get("goal_reached")),
            "reason": str(data.get("reason", "")),
        }

    # -------------------------------------------------------- legacy helpers
    async def _identify_and_switch_circuit(
        self,
        task: TaskRecord,
        circuit_list: list[Any],
        screenshot_bytes: bytes | None = None,
    ) -> str | None:
        """使用 Gemini Flash 确定并切换到目标电路，切换完成后做二次验证：
        1. `switch_circuit` API 返回 status=ok
        2. 新的 `get_io` 能返回非空的引脚列表（空列表通常意味着切换失败或选错电路）
        若校验失败，记录原因并返回 None 由 ReAct 主循环重新决策。
        """
        prompt_path = self.prompt_dir / "verification" / "switch.txt"
        prompt_text = self._load_prompt(
            prompt_path,
            task_name=task.task_name,
            goal=task.analysis_raw,
            target_subcircuit=task.target_subcircuit or "未指定",
            circuit_list=json.dumps(circuit_list, ensure_ascii=False),
        )

        if screenshot_bytes:
            img = PIL.Image.open(io.BytesIO(screenshot_bytes))
            contents: Any = [
                img,
                prompt_text
                + "\n\n注意：上图是上一轮选择的电路截图，说明该电路可能选错了。"
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
            if not extracted:
                print("[Warning] 无法从 LLM 响应中提取切换指令。")
                return None
            cmd = json.loads(extracted)
            if isinstance(cmd, list):
                cmd = cmd[0]
            action = cmd.get("action")
            name = cmd.get("name")
            if action != "switch_circuit" or not name:
                print(f"[Warning] 识别出的切换指令无效: {cmd}")
                return None

            # 检查候选电路是否真实存在
            available = [str(c) for c in (circuit_list or [])]
            if available and name not in available:
                print(
                    f"[Warning] 目标电路 {name!r} 不在可用列表 {available} 中，放弃切换。"
                )
                return None

            print(f"[Verification] 正在切换到电路: {name}")
            switch_resp = await self.emulator.send_command("switch_circuit", name=name)
            if not isinstance(switch_resp, dict) or switch_resp.get("status") != "ok":
                msg = (
                    switch_resp.get("message")
                    if isinstance(switch_resp, dict)
                    else str(switch_resp)
                )
                print(f"[Warning] switch_circuit 返回失败: {msg}")
                return None

            # 二次验证：get_io 能正常返回引脚信息
            io_resp = await self.emulator.send_command("get_io")
            if not isinstance(io_resp, dict) or io_resp.get("status") != "ok":
                msg = (
                    io_resp.get("message")
                    if isinstance(io_resp, dict)
                    else str(io_resp)
                )
                print(f"[Warning] 切换后 get_io 失败: {msg}")
                return None

            io_payload = io_resp.get("payload") or {}
            all_labels: list[Any] = []
            if isinstance(io_payload, dict):
                all_labels = list(io_payload.get("all_labeled") or [])
                if not all_labels:
                    all_labels = list(io_payload.get("inputs") or []) + list(
                        io_payload.get("outputs") or []
                    )
            if not all_labels:
                print(
                    f"[Warning] 切换后子电路 {name!r} 未发现任何标注引脚，可能选错目标。"
                )
                return None

            print(
                f"[Verification] 切换验证通过: circuit={name!r}, labels={len(all_labels)} 个。"
            )
            return name
        except Exception as e:
            print(f"[Error] 执行电路识别切换失败: {e}")
            return None

    def _extract_json(self, text: str) -> str:
        """仅剥离 ```json ... ``` / ``` ... ``` 围栏，剩下的交给调用方 json.loads。

        之前的实现会循环用 JSONDecoder.raw_decode 扫描所有 `[` / `{`，
        这在 JSON 截断时会悄悄返回半截 JSON，掩盖真正的 LLM 输出问题。
        """
        import re as _re

        if not text:
            return ""
        text = text.strip()
        m = _re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, _re.DOTALL)
        if m:
            text = m.group(1).strip()
        return text

    async def _generate_wrapped_analysis_with_retry(
        self, image, task_desc: str, max_retries: int = 2
    ) -> str:
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
