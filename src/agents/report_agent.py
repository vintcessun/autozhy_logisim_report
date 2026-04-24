import asyncio
import logging
import re
import json
import copy
import shutil
from pathlib import Path
from typing import List, Dict, Any
import pdfplumber
import PIL.Image
from docx import Document

# 静默 pdfminer 的字体元数据警告（FontBBox 缺失），不影响文字提取
logging.getLogger("pdfminer").setLevel(logging.ERROR)

from ..core.models import TaskRecord
from ..utils.ai_utils import generate_content_with_tools
from ..utils.docx_outline import llm_parse_outline, format_outline_for_prompt


class ReportAgent:
    """
    实验报告生成智能体
    职责：
    1. 提取实验环境与目的（Pro 模型，读取指导书/参考报告）。
    2. 识别挑战性任务（Pro 模型）。
    3. 生成细粒度实验分析文字（Flash 模型）。
    4. 汇总生成 Markdown 报告，整合截图与电路文件。
    """

    def __init__(self, client, model_pro: str, model_flash: str):
        self.client = client
        self.model_pro = model_pro
        self.model_flash = model_flash
        self.project_root = Path(__file__).parents[2]
        self.prompt_dir = self.project_root / "prompts"

    async def generate(
        self,
        verification_tasks: List[TaskRecord],
        design_tasks: List[TaskRecord],
        design_sub_tasks: List[TaskRecord],
        instruction_docs: List[str],
        reference_reports: List[str],
        output_path: Path,
    ) -> Path:
        """主入口：按照指导书 docx 大纲生成 Markdown 实验报告。"""
        print("\n[ReportAgent] 开始生成实验报告...")

        # 1. 提取文本 + 解析 docx 大纲
        ref_content = self._extract_docs_text(instruction_docs + reference_reports)
        outline = await llm_parse_outline(instruction_docs, self.client, self.model_pro)
        if not outline:
            print("[ReportAgent] 警告: 未能解析 docx 大纲，回退到平铺格式。")

        # 2. 实验环境 / 目的 / 摘要
        intro_data = await self._generate_intro(ref_content)
        if isinstance(intro_data, list):
            intro_data = intro_data[0] if intro_data else {}

        # 3. 覆盖式拷贝截图到 output 目录
        assets_dir = output_path.parent / "实验报告.assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        all_tasks_for_assets = verification_tasks + design_tasks + design_sub_tasks
        self._copy_assets(all_tasks_for_assets, assets_dir, overwrite=True)

        # 4. 组装报告头
        abstract_text = intro_data.get("abstract") or "计算机组成原理实验报告"
        report_md = f"# {abstract_text}\n\n"
        report_md += (
            f"## 1. 实验环境\n\n"
            f"{intro_data.get('experiment_environment', 'Windows系统下运行Logisim软件（需安装JDK）。')}\n\n"
        )
        report_md += (
            f"## 2. 实验目的\n\n"
            f"{intro_data.get('experiment_objective', '验证与设计计算机组成原理相关电路。')}\n\n"
        )
        report_md += "## 3. 实验内容\n\n"

        # 5. 将所有 TaskRecord 按 docx 大纲分配到各 section/group
        if outline:
            section_assignments = await self._assign_tasks_to_outline(
                outline=outline,
                verification_tasks=verification_tasks,
                design_tasks=design_tasks,
            )
        else:
            section_assignments = {}

        # 6. 收集风格上下文：供后续每个切片分析时参考，保证整体风格一致
        style_context = {
            "abstract": abstract_text,
            "objective": intro_data.get("experiment_objective", ""),
            "tone": (
                "中文、学术、简洁；围绕截图可见的数值/信号/时序直接分析，避免空话套话；"
                "不要出现'截图''本图'字样；每段 120-220 字。"
            ),
        }

        # 7. 按 docx outline 渲染
        if outline:
            for sec in outline:
                report_md += f"## {sec['num']} {sec['title']}\n\n"
                groups = section_assignments.get(sec["num"], [])
                rendered_any = False
                for group_info in groups:
                    rendered = await self._render_outline_group(
                        section_num=sec["num"],
                        group=group_info["group"],
                        tasks=group_info["tasks"],
                        design_sub_tasks=design_sub_tasks,
                        style_context=style_context,
                    )
                    if rendered:
                        report_md += rendered
                        rendered_any = True
                if not rendered_any:
                    report_md += "_（本节无匹配的实验记录）_\n\n"
        else:
            # 回退：旧平铺格式
            report_md += await self._render_fallback(
                verification_tasks, design_tasks, design_sub_tasks, style_context
            )

        # 8. 保存
        output_path.write_text(report_md, encoding="utf-8")
        print(f"[ReportAgent] 实验报告已生成: {output_path}")
        return output_path

    # ------------------------------------------------------------------ #
    # 大纲驱动渲染
    # ------------------------------------------------------------------ #
    async def _assign_tasks_to_outline(
        self,
        outline: List[Dict[str, Any]],
        verification_tasks: List[TaskRecord],
        design_tasks: List[TaskRecord],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """让 LLM 把每个 TaskRecord 映射到 outline 的某个 group。

        返回: { section_num: [ {group, tasks:[TaskRecord]} ] }
        - 3.1 内的 group 保留所有 group（哪怕无 tasks，也以 `finished` 截图占位说明）。
        - 3.2 / 3.3 内只保留至少有 1 个 task 命中的 group（用户说 3.3 常是"任选一组"）。
        """

        # 构造可序列化的 task 摘要供 LLM 判别
        def _task_digest(t: TaskRecord) -> Dict[str, Any]:
            return {
                "task_id": t.task_id,
                "task_name": t.task_name,
                "task_type": t.task_type,
                "target_subcircuit": t.target_subcircuit,
                "source_circ": [Path(p).name for p in (t.source_circ or [])],
                "reference_circ": (
                    Path(t.reference_circ).name if t.reference_circ else None
                ),
                "section_text": (t.section_text or "")[:200],
                "status": t.status,
            }

        all_tasks = list(verification_tasks) + list(design_tasks)
        task_digests = [_task_digest(t) for t in all_tasks]
        outline_text = format_outline_for_prompt(outline)

        # 所有合法 group_id
        valid_gids: List[str] = []
        for sec in outline:
            for g in sec["groups"]:
                valid_gids.append(f"{sec['num']}-{g['index']}")

        base_prompt = (
            "你是实验报告编排助手。请把以下 TaskRecord 逐个映射到指导书大纲里最合适的"
            " group_id（形如 '3.1-1'）。\n"
            "匹配原则：\n"
            "  1. 以 task_name / target_subcircuit / source_circ 文件名 与 group 描述里的关键词做语义匹配；\n"
            "  2. verification 类型的任务优先归到 3.1 下对应 group；\n"
            "  3. design 类型优先归到 3.2 对应 group；\n"
            "  4. challenge 类型归到 3.3；\n"
            "  5. **同一个 group 的描述可能包含多个用顿号/逗号分隔的子项（例如 "
            "'直接相联 cache、全相联 cache、2路组相联 cache ...'），"
            "只要任务与其中任何一个子项匹配，就归到该 group。不要因为 group 描述罗列了多个方案就把任务挤到只有一个 group。**\n"
            "  6. **每一个 task_id 都必须分配到一个合法 group_id，禁止返回 null 或遗漏。**"
            "     如果无法精确匹配，选择语义最接近的 group（例如 section 层级正确但 group 描述不完全吻合时，"
            "     也必须选一个最接近的 group_id）。\n\n"
            "严格输出 JSON：\n"
            '{"assignments": [{"task_id": "...", "group_id": "3.1-1"}]}\n'
            "assignments 数组必须覆盖下面【任务列表】中的每一个 task_id，不能遗漏，不能出现 null。\n\n"
            f"【大纲（合法 group_id 列表）】\n{outline_text}\n\n"
            f"【所有合法 group_id】{json.dumps(valid_gids, ensure_ascii=False)}\n\n"
            f"【任务列表】\n{json.dumps(task_digests, ensure_ascii=False, indent=2)}\n"
        )

        task_to_group: Dict[str, str] = {}
        all_task_ids = {t.task_id for t in all_tasks}
        max_llm_retries = 3
        last_error: str = ""
        for attempt in range(max_llm_retries):
            if attempt == 0:
                prompt = base_prompt
            else:
                unassigned = [tid for tid in all_task_ids if tid not in task_to_group]
                unassigned_digests = [
                    _task_digest(t) for t in all_tasks if t.task_id in unassigned
                ]
                prompt = (
                    f"上一次分配不完整: {last_error}\n"
                    "以下 task_id 仍未分配，请务必为它们每一个都给出一个合法 group_id（来自下方列表），"
                    "不要返回 null，也不要遗漏。\n\n"
                    f"【未分配任务】\n{json.dumps(unassigned_digests, ensure_ascii=False, indent=2)}\n\n"
                    f"【大纲】\n{outline_text}\n\n"
                    f"【所有合法 group_id】{json.dumps(valid_gids, ensure_ascii=False)}\n\n"
                    '严格输出 JSON：{"assignments": [{"task_id": "...", "group_id": "3.x-k"}]}'
                )

            try:
                response = await generate_content_with_tools(
                    self.client,
                    model=self.model_pro,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                raw = (response.text or "").strip()
                data = json.loads(raw)
                if isinstance(data, list) and data:
                    data = data[0]
                for item in data.get("assignments", []):
                    gid = item.get("group_id")
                    tid = item.get("task_id")
                    if tid and gid and gid in valid_gids:
                        task_to_group[tid] = gid
            except Exception as e:
                last_error = f"LLM 调用/解析失败: {e}"
                print(f"[ReportAgent] 第 {attempt + 1} 轮大纲分配异常: {e}")
                continue

            missing = all_task_ids - set(task_to_group.keys())
            if not missing:
                break
            last_error = f"still missing {len(missing)} task(s): " + json.dumps(
                sorted(missing), ensure_ascii=False
            )
            print(
                f"[ReportAgent] 第 {attempt + 1} 轮后仍有 {len(missing)} 个任务未归类，继续重试。"
            )

        # 关键词兜底：LLM 仍未分配 → 用 source_circ / target_subcircuit 与 group 子串匹配
        for t in all_tasks:
            if t.task_id in task_to_group:
                continue
            gid = self._fallback_match_group(t, outline)
            if gid:
                task_to_group[t.task_id] = gid

        # 最终强校验：任何 TaskRecord 都必须被分配，否则报错
        missing_final = [t for t in all_tasks if t.task_id not in task_to_group]
        if missing_final:
            details = "\n".join(
                f"  - {t.task_id} ({t.task_type}) {t.task_name}" for t in missing_final
            )
            raise RuntimeError(
                "ReportAgent 任务归类失败：以下 TaskRecord 无法匹配到任何 docx 大纲 group，"
                "请检查指导书大纲或任务元数据后重试：\n" + details
            )

        # 构造 section → [{group, tasks}]
        result: Dict[str, List[Dict[str, Any]]] = {}
        task_lookup = {t.task_id: t for t in all_tasks}
        for sec in outline:
            sec_entries: List[Dict[str, Any]] = []
            for g in sec["groups"]:
                gid = f"{sec['num']}-{g['index']}"
                matched = [
                    task_lookup[tid]
                    for tid, assigned in task_to_group.items()
                    if assigned == gid and tid in task_lookup
                ]
                # 保留所有 group（即使空），便于暴露归类遗漏；空组渲染时会给占位提示
                sec_entries.append({"group": g, "tasks": matched})
            result[sec["num"]] = sec_entries

        # 诊断打印：每个 group 的任务分配概览
        print("[ReportAgent] 大纲分配结果：")
        for sec_num, entries in result.items():
            for e in entries:
                g = e["group"]
                names = [t.task_name for t in e["tasks"]]
                print(
                    f"  {sec_num}-{g['index']} {g['description'][:40]!r} → "
                    f"{len(names)} task(s): {names}"
                )
        return result

    def _fallback_match_group(
        self, task: TaskRecord, outline: List[Dict[str, Any]]
    ) -> str | None:
        """基于关键词的粗匹配：任务名 / 目标子电路 / 源文件名 命中 group description 则选中。"""
        keywords = [
            task.task_name or "",
            task.target_subcircuit or "",
        ] + [Path(p).stem for p in (task.source_circ or [])]
        keywords = [k.strip() for k in keywords if k and k.strip()]
        if not keywords:
            return None

        # 任务类型→优先 section
        prefer_sec = {
            "verification": "3.1",
            "design": "3.2",
            "challenge": "3.3",
        }.get(task.task_type)

        def _score(section_num: str, desc: str) -> int:
            s = 0
            for kw in keywords:
                for token in re.split(r"[\s、，,()（）]+", kw):
                    token = token.strip()
                    if len(token) >= 3 and token in desc:
                        s += len(token)
            if prefer_sec and section_num == prefer_sec:
                s += 5
            return s

        best_gid = None
        best_score = 0
        for sec in outline:
            for g in sec["groups"]:
                score = _score(sec["num"], g["description"])
                if score > best_score:
                    best_score = score
                    best_gid = f"{sec['num']}-{g['index']}"
        return best_gid if best_score > 0 else None

    async def _render_outline_group(
        self,
        section_num: str,
        group: Dict[str, Any],
        tasks: List[TaskRecord],
        design_sub_tasks: List[TaskRecord],
        style_context: Dict[str, Any],
    ) -> str:
        """按 docx 分点格式渲染单个 group。

        层级结构：
          ### ({index}) {description}               — 组（L3）
          #### {task.task_name}                     — 任务（L4，验证）
          ##### 截图 / 实验分析                       — 任务正文（L5）
          #### 电路截图                               — 设计/挑战的参考电路图（L4）
          #### {sub.task_name}                      — 设计/挑战拆出的验证子任务（L4）
          ##### 截图 / 实验分析                       — 子任务正文（L5）
          #### 回答问题                               — 分组思考题（L4）
          ##### {question}                           — 单题（L5）
        """
        md = f"### ({group['index']}) {group['description']}\n\n"

        if not tasks:
            md += "_（本组无实验记录）_\n\n"
            return md

        assets_rel = "./实验报告.assets"

        def _emit_task_block(
            task_name: str,
            images: List[str],
            analysis: str,
        ) -> str:
            block = f"#### {task_name}\n\n"
            block += "##### 截图\n\n"
            if images:
                for asset in images:
                    block += f"![{task_name}]({assets_rel}/{Path(asset).name})\n\n"
            else:
                block += "_（暂无截图）_\n\n"
            block += "##### 实验分析\n\n"
            block += f"{analysis.strip() if analysis else '_（暂无分析）_'}\n\n"
            return block

        for t in tasks:
            if t.task_type in ("design", "challenge"):
                # 设计 / 挑战：先放参考电路截图，再逐个展示其验证子任务
                if t.assets:
                    ref_img = Path(t.assets[0]).name
                    md += "#### 电路截图\n\n"
                    md += f"![{t.task_name} 参考电路]({assets_rel}/{ref_img})\n\n"
                relevant_subs = [
                    s for s in design_sub_tasks if s.source_circ == t.source_circ
                ]
                for sub in relevant_subs:
                    sub_analysis = await self._generate_task_analysis(sub)
                    md += _emit_task_block(
                        task_name=sub.task_name,
                        images=list(sub.assets or []),
                        analysis=sub_analysis,
                    )
                # 若 design 任务本身带有额外分析（DesignAgent 汇总的），也作为一个块输出
                if (t.analysis_raw or "").strip():
                    md += f"#### {t.task_name}\n\n"
                    md += "##### 实验分析\n\n"
                    md += f"{t.analysis_raw.strip()}\n\n"
            else:
                # 验证：单任务块
                analysis = await self._generate_task_analysis(t)
                md += _emit_task_block(
                    task_name=t.task_name,
                    images=list(t.assets or []),
                    analysis=analysis,
                )

        # 回答问题
        questions = group.get("questions") or []
        if questions:
            md += "#### 回答问题\n\n"
            for q in questions:
                answer = await self._answer_question_for_group(
                    question=q,
                    group=group,
                    tasks=tasks,
                    style_context=style_context,
                )
                md += f"##### {q}\n\n{answer}\n\n"

        return md

    async def _generate_group_analysis(
        self,
        group: Dict[str, Any],
        tasks: List[TaskRecord],
        design_sub_tasks: List[TaskRecord],
        style_context: Dict[str, Any],
    ) -> str:
        """整合该 group 下所有 task 的 analysis_raw + 截图，生成统一风格的最终分析。"""
        # 汇总初步分析
        raw_parts: List[str] = []
        image_paths: List[Path] = []
        for t in tasks:
            if t.analysis_raw:
                raw_parts.append(f"【{t.task_name}】{t.analysis_raw}")
            for a in t.assets:
                p = Path(a)
                if p.exists():
                    image_paths.append(p)
            if t.task_type in ("design", "challenge"):
                for sub in design_sub_tasks:
                    if sub.source_circ != t.source_circ:
                        continue
                    if sub.analysis_raw:
                        raw_parts.append(f"【{sub.task_name}】{sub.analysis_raw}")
                    for a in sub.assets:
                        p = Path(a)
                        if p.exists():
                            image_paths.append(p)

        # 截图数量限制，避免请求过大
        image_paths = image_paths[:4]
        preliminary = "\n\n".join(raw_parts) if raw_parts else "（暂无初步分析）"

        prompt_text = (
            "你是实验报告撰写专家。请为下述实验分组撰写一段专业、客观的【实验分析】正文。\n"
            f"【报告风格要求】{style_context.get('tone')}\n"
            f"【整体实验目的】{style_context.get('objective')}\n"
            f"【当前分组的实验描述】{group['description']}\n"
            "【要求】\n"
            "  - 直接分析附带图像中可见的信号/数值/时序表现；\n"
            "  - 融合下面已生成的初步分析内容，但要修正其中空泛或与图像不符的表述；\n"
            "  - 一段 200-400 字，不要标题、不要列表项编号、不要出现'截图''本图'字样；\n"
            "  - 如果含多个子实验，用一个段落统摄，不要简单拼接。\n"
            f"【初步分析（来自各 task 的 analysis_raw）】\n{preliminary}\n"
            "\n请严格使用包裹格式：\n--BEGIN--\n正文\n--END--"
        )

        contents: List[Any] = []
        for p in image_paths:
            try:
                contents.append(PIL.Image.open(p))
            except Exception as e:
                print(f"[ReportAgent] 打开图像 {p} 失败: {e}")
        contents.append(prompt_text)

        try:
            return await self._generate_wrapped_markdown(
                contents=contents,
                model=self.model_pro,
            )
        except Exception as e:
            print(
                f"[ReportAgent] 生成分组分析失败 ({group.get('description','')[:30]}): {e}"
            )
            return preliminary

    async def _answer_question_for_group(
        self,
        question: str,
        group: Dict[str, Any],
        tasks: List[TaskRecord],
        style_context: Dict[str, Any],
    ) -> str:
        """针对 docx 中的每个问题生成回答，结合当前 group 的任务上下文。"""
        task_summary = (
            "\n".join(
                f"- {t.task_name}: {t.analysis_raw[:200]}"
                for t in tasks
                if t.analysis_raw
            )
            or "（无可用实验结论）"
        )

        prompt = (
            "你是数字电路实验报告助手。针对下面的问题给出简洁、专业、可直接放入实验报告正文的回答。\n"
            f"【报告风格要求】{style_context.get('tone')}\n"
            f"【当前分组描述】{group['description']}\n"
            f"【相关实验结论摘要】\n{task_summary}\n"
            f"【问题】{question}\n"
            "【要求】\n"
            "  - 直接回答问题，不要复述问题；\n"
            "  - 结合电路原理与实验观测，必要时举具体数值；\n"
            "  - 120-300 字，一到两段；不要标题。\n"
            "\n请严格使用包裹格式：\n--BEGIN--\n正文\n--END--"
        )
        try:
            return await self._generate_wrapped_markdown(
                contents=prompt,
                model=self.model_pro,
            )
        except Exception as e:
            print(f"[ReportAgent] 回答问题失败: {e}")
            return ""

    async def _render_fallback(
        self,
        verification_tasks: List[TaskRecord],
        design_tasks: List[TaskRecord],
        design_sub_tasks: List[TaskRecord],
        style_context: Dict[str, Any],
    ) -> str:
        """docx 解析失败时的回退：仍按 3.1 / 3.2 / 3.3 粗分类，平铺各 task。

        层级与大纲路径保持一致：
          ### 组 → #### 任务 / 电路截图 → ##### 截图 / 实验分析。
        """
        assets_rel = "./实验报告.assets"

        def _emit_task_block(task_name: str, images: List[str], analysis: str) -> str:
            block = f"#### {task_name}\n\n"
            block += "##### 截图\n\n"
            if images:
                for asset in images:
                    block += f"![{task_name}]({assets_rel}/{Path(asset).name})\n\n"
            else:
                block += "_（暂无截图）_\n\n"
            block += "##### 实验分析\n\n"
            block += f"{analysis.strip() if analysis else '_（暂无分析）_'}\n\n"
            return block

        md = "## 3.1 验证性实验\n\n"
        for i, task in enumerate(verification_tasks, 1):
            analysis = await self._generate_task_analysis(task)
            md += f"### ({i}) {task.task_name}\n\n"
            md += _emit_task_block(
                task_name=task.task_name,
                images=list(task.assets or []),
                analysis=analysis,
            )

        md += "## 3.2 设计实验\n\n"
        for i, task in enumerate(
            [t for t in design_tasks if t.task_type == "design"], 1
        ):
            md += await self._build_design_section(task, design_sub_tasks, i)

        challenge_tasks = [t for t in design_tasks if t.task_type == "challenge"]
        if challenge_tasks:
            md += "## 3.3 挑战性实验\n\n"
            for i, task in enumerate(challenge_tasks, 1):
                md += await self._build_design_section(task, design_sub_tasks, i)
        return md

    async def _build_design_section(
        self, task: TaskRecord, sub_tasks: List[TaskRecord], idx: int
    ) -> str:
        """构建单个设计/挑战性实验小节。

        层级：
          ### (idx) {task_name}           — 实验组
          #### 电路截图                   — 参考电路图
          #### {sub.task_name}           — 子验证任务
          ##### 截图 / 实验分析
          #### {task_name}               — 汇总分析（若有）
          ##### 实验分析
          #### 回答问题
          ##### {question}
        """
        assets_rel = "./实验报告.assets"
        section = f"### ({idx}) {task.task_name}\n\n"

        if task.assets:
            ref_img = Path(task.assets[0]).name
            section += "#### 电路截图\n\n"
            section += f"![{task.task_name} 参考电路]({assets_rel}/{ref_img})\n\n"

        relevant_subs = [s for s in sub_tasks if s.source_circ == task.source_circ]
        for sub in relevant_subs:
            sub_analysis = await self._generate_task_analysis(sub)
            section += f"#### {sub.task_name}\n\n"
            section += "##### 截图\n\n"
            if sub.assets:
                for asset in sub.assets:
                    section += (
                        f"![{sub.task_name}]({assets_rel}/{Path(asset).name})\n\n"
                    )
            else:
                section += "_（暂无截图）_\n\n"
            section += "##### 实验分析\n\n"
            section += (
                f"{sub_analysis.strip() if sub_analysis else '_（暂无分析）_'}\n\n"
            )

        if (task.analysis_raw or "").strip():
            section += f"#### {task.task_name}\n\n"
            section += "##### 实验分析\n\n"
            section += f"{task.analysis_raw.strip()}\n\n"

        answered_problems = await self._generate_problem_answers(task)
        task.problem_answers = answered_problems
        if answered_problems:
            section += "#### 回答问题\n\n"
            for item in answered_problems:
                question = item.get("problem", "").strip() or "（未指定问题）"
                answer = (item.get("answer") or "").strip()
                section += f"##### {question}\n\n{answer}\n\n"
        return section

    def _extract_docs_text(self, doc_paths: List[str]) -> str:
        """从 PDF/DOCX 中提取文本"""
        all_text = ""
        for path_str in doc_paths:
            path = Path(path_str)
            if not path.exists():
                continue
            try:
                if path.suffix.lower() == ".pdf":
                    with pdfplumber.open(path) as pdf:
                        for page in pdf.pages:
                            all_text += (page.extract_text() or "") + "\n"
                elif path.suffix.lower() == ".docx":
                    doc = Document(path)
                    all_text += "\n".join([p.text for p in doc.paragraphs]) + "\n"
                elif path.suffix.lower() == ".txt":
                    all_text += path.read_text(encoding="utf-8") + "\n"
            except Exception as e:
                print(f"[ReportAgent] 提取文档 {path.name} 失败: {e}")
        return all_text

    async def _generate_intro(self, ref_content: str) -> Dict:
        """Phase A: 实验环境、目的、摘要"""
        prompt_path = self.prompt_dir / "report" / "intro.txt"
        if not prompt_path.exists():
            return {}

        prompt = prompt_path.read_text(encoding="utf-8").replace(
            "{reference_content}", ref_content[:15000]
        )

        try:
            response = await generate_content_with_tools(
                self.client,
                model=self.model_pro,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return json.loads(response.text.strip())
        except Exception as e:
            print(f"[ReportAgent] 生成 Intro 数据失败: {e}")
            return {}

    async def _split_challenge_tasks(self, design_tasks: List[TaskRecord]) -> Dict:
        """Phase A: 挑战性任务识别"""
        if not design_tasks:
            return {"section_32_ids": [], "section_33_ids": []}

        # 如果只有一个设计任务，默认归为 3.2
        if len(design_tasks) == 1:
            return {"section_32_ids": [design_tasks[0].task_id], "section_33_ids": []}

        prompt_path = self.prompt_dir / "report" / "challenge_split.txt"
        if not prompt_path.exists():
            return {
                "section_32_ids": [t.task_id for t in design_tasks],
                "section_33_ids": [],
            }

        task_list_str = "\n".join(
            [
                f"- ID: {t.task_id}, Name: {t.task_name}, Type: {t.task_type}"
                for t in design_tasks
            ]
        )
        prompt = prompt_path.read_text(encoding="utf-8").replace(
            "{task_list}", task_list_str
        )

        try:
            response = await generate_content_with_tools(
                self.client,
                model=self.model_pro,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return json.loads(response.text.strip())
        except Exception as e:
            print(f"[ReportAgent] 识别挑战性任务失败: {e}")
            return {
                "section_32_ids": [t.task_id for t in design_tasks],
                "section_33_ids": [],
            }

    async def _generate_task_analysis(self, task: TaskRecord) -> str:
        """Phase B: 生成每个子任务的一段分析文字"""
        prompt_path = self.prompt_dir / "report" / "analysis.txt"
        if not prompt_path.exists():
            return task.analysis_raw

        prompt = (
            prompt_path.read_text(encoding="utf-8")
            .replace("{task_name}", task.task_name or "")
            .replace("{section_text}", task.section_text or "")
            .replace("{analysis_raw}", task.analysis_raw or "")
        )

        wrapped_prompt = (
            prompt
            + "\n\n请严格使用以下包裹格式输出：\n"
            + "--BEGIN--\n"
            + "正文（可用 Markdown，但不要包含标题）\n"
            + "--END--"
        )

        try:
            return await self._generate_wrapped_markdown(
                contents=wrapped_prompt,
                model=self.model_flash,
            )
        except Exception as e:
            print(f"[ReportAgent] 生成任务分析失败 ({task.task_name}): {e}")
            return task.analysis_raw

    async def _generate_problem_answers(self, task: TaskRecord) -> List[dict[str, str]]:
        """基于任务上下文回答题目列表。"""
        if not task.problem_answers:
            return []

        answered = copy.deepcopy(task.problem_answers)
        for item in answered:
            problem = item.get("problem", "").strip()
            if not problem:
                continue
            prompt = (
                "你是数字电路实验报告助手。请针对下面的问题给出简洁、专业、可直接放入实验报告正文的回答。\n"
                "请严格使用以下包裹格式输出：\n"
                "--BEGIN--\n"
                "正文（可用 Markdown，但不要包含标题）\n"
                "--END--\n\n"
                f"实验名称: {task.task_name}\n"
                f"实验要求原文: {task.section_text}\n"
                f"实验目标: {task.experiment_objective}\n"
                f"实验分析: {task.analysis_raw}\n"
                f"问题: {problem}\n"
            )
            try:
                item["answer"] = await self._generate_wrapped_markdown(
                    contents=prompt,
                    model=self.model_pro,
                )
            except Exception as e:
                print(f"[ReportAgent] 回答问题失败 ({task.task_name}): {e}")
                item["answer"] = ""
        return answered

    async def _generate_wrapped_markdown(
        self, contents, model: str, max_retries: int = 2
    ) -> str:
        """生成严格 BEGIN/END 包裹的 Markdown 正文。"""
        last_error = ""
        last_raw = ""

        for attempt in range(max_retries + 1):
            current_contents = contents
            if attempt > 0:
                fix_prompt = (
                    "你上一次输出不符合格式要求。请只输出严格包裹格式：\n"
                    "--BEGIN--\n"
                    "正文（可用 Markdown，但不要包含标题）\n"
                    "--END--\n\n"
                    f"上一次错误：{last_error}\n"
                    f"上一次输出：\n{last_raw[:1500]}\n"
                )
                if isinstance(contents, list):
                    current_contents = contents + [fix_prompt]
                else:
                    current_contents = f"{contents}\n\n{fix_prompt}"

            response = await generate_content_with_tools(
                self.client,
                model=model,
                contents=current_contents,
            )
            raw = (response.text or "").strip()
            try:
                return self._extract_wrapped_markdown(raw, strict=True)
            except ValueError as e:
                last_error = str(e)
                last_raw = raw
                print(
                    f"[ReportAgent] Markdown 格式校验失败（第 {attempt+1} 次），准备重试: {e}"
                )

        raise RuntimeError(
            "ReportAgent 生成 Markdown 失败：模型多次未按 --BEGIN--/--END-- 返回。"
        )

    def _extract_wrapped_markdown(self, text: str, strict: bool = False) -> str:
        """提取 --BEGIN-- 与 --END-- 之间的 Markdown 正文。"""
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

    def _copy_assets(
        self, tasks: List[TaskRecord], assets_dir: Path, overwrite: bool = False
    ):
        """将所有截图拷贝到 output/实验报告.assets/。overwrite=True 时覆盖同名文件。"""
        for task in tasks:
            for asset_path in task.assets:
                src = Path(asset_path)
                if not src.exists():
                    # 尝试在 output 目录下寻找
                    maybe_src = Path("output") / asset_path
                    if maybe_src.exists():
                        src = maybe_src

                if src.exists():
                    try:
                        dst = assets_dir / src.name
                        if src.resolve() == dst.resolve():
                            continue
                        if overwrite and dst.exists():
                            try:
                                dst.unlink()
                            except Exception:
                                pass
                        shutil.copy2(src, dst)
                    except shutil.SameFileError:
                        pass
                    except Exception as e:
                        print(f"[ReportAgent] 拷贝截图 {src.name} 失败: {e}")
                else:
                    print(f"[ReportAgent] 警告: 找不到截图 {asset_path}")
