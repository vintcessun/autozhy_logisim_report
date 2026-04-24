import os
import shutil
import zipfile
import re
import json
from pathlib import Path
import pdfplumber
from docx import Document
from typing import List, Dict, Optional, Tuple, Any
from ..core.models import TaskRecord, ParsingResult
from ..utils.ai_utils import generate_content_with_tools


class DataDecompressor:
    """递归解压工具，支持处理中文乱码 (GBK 编码)"""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def unzip_recursive(self, src_path: Path):
        """
        递归解压并将所有内容平铺到 workspace 目录。
        解决 zipfile 在 Windows 上处理中文名乱码的问题。
        """
        if not zipfile.is_zipfile(src_path):
            return

        ref_pattern = re.compile(r"^\d+[\+\s\-_]+[\u4e00-\u9fa5]+")
        is_reference = ref_pattern.search(src_path.name) is not None

        try:
            with zipfile.ZipFile(src_path, "r") as zip_ref:
                for info in zip_ref.infolist():
                    # 关键点：处理文件名编码
                    # ZipFile 默认使用 cp437，中文文件名通常需要转为 gbk
                    try:
                        filename = info.filename.encode("cp437").decode("gbk")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        filename = info.filename

                    if info.is_dir():
                        continue

                    # 提取基础文件名，忽略目录结构
                    bare_name = Path(filename).name
                    if not bare_name:
                        continue

                    prefix = "REF_" if is_reference else "TEA_"
                    target_name = f"{prefix}{bare_name}"
                    target_path = self.workspace_dir / target_name

                    if target_path.exists():
                        target_path = (
                            self.workspace_dir / f"{prefix}{src_path.stem}_{bare_name}"
                        )

                    # 执行解压并重命名
                    with (
                        zip_ref.open(info) as source,
                        open(target_path, "wb") as target,
                    ):
                        shutil.copyfileobj(source, target)

                    # 递归处理提取出来的 ZIP (如果有的话)
                    if target_path.suffix.lower() == ".zip":
                        self.unzip_recursive(target_path)

        except Exception as e:
            raise RuntimeError(f"解压 {src_path.name} 失败: {e}") from e

    def _is_zip(self, file_path: Path) -> bool:
        """判断是否为 ZIP 格式"""
        return file_path.suffix.lower() == ".zip"


class RequirementExtractor:
    """需求提取器，利用 LLM 进行关联匹配"""

    def __init__(self, client, model_name: str):
        self.client = client
        self.model_id = model_name

    def extract_text_from_pdf(self, pdf_path: Path) -> str:
        """提取 PDF 文本"""
        text = ""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
        except Exception as e:
            raise RuntimeError(f"无法从 PDF {pdf_path.name} 提取文本: {e}") from e
        return text

    def extract_text_from_docx(self, docx_path: Path) -> str:
        """提取 DOCX 文本"""
        try:
            doc = Document(docx_path)
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            raise RuntimeError(f"无法从 DOCX {docx_path.name} 提取文本: {e}") from e

    @staticmethod
    def _strip_markdown_fence(raw: str) -> str:
        """仅剥离 ```json ... ``` / ``` ... ``` 外壳，其它一律原样返回。

        之前的实现会用正则暴力抠第一个 {...} 或 [...]，这会在 JSON 截断时
        悄悄丢掉后半段内容，导致下游看不到真正的错误。这里只做最无损的
        处理：去掉围栏。模型输出本身是否合法 JSON 交给调用方直接 json.loads
        去判断，失败就让模型在同一对话里自己修复。
        """
        import re as _re

        text = raw.strip()
        md_fence = _re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, _re.DOTALL)
        if md_fence:
            text = md_fence.group(1).strip()
        return text

    async def _call_with_json_retry(
        self,
        prompt: str,
        context_label: str,
        max_json_retries: int = 3,
        model_id: Optional[str] = None,
    ) -> str:
        """调用 LLM 并确保返回合法 JSON 字符串。

        策略极简：
        - 剥掉 markdown 围栏后直接 `json.loads`。
        - 失败时把 Python 的报错消息原样塞回**同一个会话**，让模型自己修。
        - 达到上限则抛 `RuntimeError`，绝不静默返回空串。
        """
        import json as _json
        from google.genai import types as genai_types

        target_model = model_id or self.model_id

        history: list[Any] = [
            genai_types.Content(
                role="user", parts=[genai_types.Part.from_text(text=prompt)]
            )
        ]

        for attempt in range(max_json_retries + 1):
            response = await generate_content_with_tools(
                self.client,
                model=target_model,
                contents=history,
                config={"response_mime_type": "application/json"},
            )
            raw = response.text or ""
            history.append(
                genai_types.Content(
                    role="model", parts=[genai_types.Part.from_text(text=raw)]
                )
            )

            candidate = self._strip_markdown_fence(raw)
            try:
                _json.loads(candidate)
                return candidate
            except Exception as e:
                parse_error = str(e)

            if attempt >= max_json_retries:
                raise RuntimeError(
                    f"[JSON 解析失败] {context_label} 达到最大重试次数 "
                    f"({max_json_retries})，最后一次错误：{parse_error}\n"
                    f"原始输出前 500 字：\n{raw[:500]}"
                )

            print(
                f"[JSON 重试] {context_label} 第 {attempt+1} 次输出非合法 JSON"
                f"（{parse_error}），在同一会话中要求模型自行修复..."
            )
            history.append(
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_text(
                            text=(
                                "你上一轮的输出不是合法 JSON，Python `json.loads` 报错："
                                f"{parse_error}\n"
                                "请基于同样的任务要求，**重新输出**一份完整、合法的 JSON。"
                                "只输出 JSON 本身，不要 markdown 围栏、不要解释、不要前言。"
                            )
                        )
                    ],
                )
            )

        raise RuntimeError(f"[JSON 解析失败] {context_label} 未能获得合法 JSON 响应")

    async def phase1_classify(
        self,
        text: str,
        prompt_template: str,
        teacher_files: List[str],
        reference_files: List[str],
        outline: Optional[List[Dict]] = None,
        partial_reparse: bool = False,
        existing_tasks_summary: str = "",
    ) -> dict:
        """阶段一：识别所有实验模块，分类并提取各模块对应的原文段落"""
        if not self.client:
            raise RuntimeError(
                "阶段一分类失败：LLM client 未初始化，无法执行内容解析。"
            )
        prompt = prompt_template.replace(
            "{{teacher_files}}", "\n".join(teacher_files) if teacher_files else "无"
        )
        prompt = prompt.replace(
            "{{reference_files}}",
            "\n".join(reference_files) if reference_files else "无",
        )

        # 把 DOCX 大纲注入 prompt，让 LLM 按大纲逐条生成而不遗漏
        if outline:
            from ..utils.docx_outline import format_outline_for_prompt

            outline_text = format_outline_for_prompt(outline)
            if partial_reparse:
                # 增量补全：只允许生成列出的组，不生成其他
                outline_block = (
                    "【增量补全模式：以下是尚未覆盖的实验组，"
                    "你只需为这些实验组生成对应的 experiments，"
                    "不得生成列表之外的任何实验，不得遗漏列表中的任何一项】\n"
                    + outline_text
                )
            else:
                outline_block = (
                    "【实验指导书大纲（必须按此完整覆盖，不得遗漏任何一项）】\n"
                    + outline_text
                )
            prompt = prompt.replace("{{outline}}", outline_text)
            if "{{outline}}" not in prompt_template:
                prompt = prompt + f"\n\n{outline_block}"
        else:
            prompt = prompt.replace("{{outline}}", "（未提供大纲）")

        if existing_tasks_summary.strip():
            prompt = (
                prompt + "\n\n【已存在的缓存任务（请勿重复生成；只生成下表之外的、确属"
                "未覆盖大纲组所需的新实验）】\n" + existing_tasks_summary
            )

        prompt = f"{prompt}\n\n待解析全文：\n{text}"

        import json as _json

        extracted = await self._call_with_json_retry(prompt, "阶段一分类")
        try:
            return _json.loads(extracted)
        except Exception as e:
            raise RuntimeError(f"阶段一最终解析失败: {e}") from e

    async def phase2_detail_verify(
        self, experiment: dict, prompt_template: str
    ) -> List[dict]:
        """阶段二：仅针对验证性实验，将其 section_text 拆解为原子测试用例列表"""
        if not self.client:
            raise RuntimeError(
                f"阶段二细化失败：LLM client 未初始化（实验='{experiment.get('name', '?')}'）。"
            )
        prompt = prompt_template.replace(
            "{{experiment_name}}", experiment.get("name", "")
        )
        prompt = prompt.replace(
            "{{source_circ}}", experiment.get("matched_source_circ") or "无"
        )
        prompt = prompt.replace(
            "{{target_subcircuit}}", experiment.get("target_subcircuit") or "main"
        )
        prompt = prompt.replace("{{section_text}}", experiment.get("section_text", ""))

        import json as _json

        label = f"阶段二[{experiment.get('name', '?')}]"
        extracted = await self._call_with_json_retry(prompt, label)
        try:
            result = _json.loads(extracted)
        except Exception as e:
            raise RuntimeError(f"{label} 最终解析失败: {e}") from e
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("tasks", result.get("items", []))
        raise RuntimeError(
            f"{label} 返回的 JSON 结构非法（既不是 list 也不是 dict）：{type(result).__name__}"
        )

    async def phase3_check_subdivision(
        self, task_desc: str, prompt_template: str
    ) -> bool:
        """阶段三检查：利用 Flash 判断是否可进一步拆分"""
        prompt = prompt_template.replace("{{task_description}}", task_desc)
        label = "[阶段三/Check]"

        import json as _json

        json_str = await self._call_with_json_retry(prompt, label)
        try:
            res = _json.loads(json_str)
        except Exception as e:
            raise RuntimeError(f"{label} JSON 解析失败: {e}") from e
        return bool(res.get("can_be_subdivided", False))

    async def phase3_split_task(
        self, task_desc: str, section_text: str, prompt_template: str, model_pro: str
    ) -> List[dict]:
        """阶段三拆分：利用 Pro 将任务拆解为子任务"""
        prompt = prompt_template.replace("{{task_description}}", task_desc)
        prompt = prompt.replace("{{section_text}}", section_text)
        label = "[阶段三/Split]"

        import json as _json

        json_str = await self._call_with_json_retry(prompt, label, model_id=model_pro)
        try:
            res = _json.loads(json_str)
        except Exception as e:
            raise RuntimeError(f"{label} JSON 解析失败: {e}") from e
        if isinstance(res, list):
            return res
        raise RuntimeError(
            f"{label} 返回的 JSON 结构非法（期望 list，实际 {type(res).__name__}）"
        )


class ContentParsingAgent:
    """内容解析智能体总控"""

    def __init__(self, config, workspace_dir: Path, client, cache=None):
        self.config = config
        self.decompressor = DataDecompressor(workspace_dir)
        self.extractor = RequirementExtractor(client, config.gemini.model_flash)
        self.workspace_dir = workspace_dir
        self.cache = cache

    @staticmethod
    def _normalize_name(text: str) -> str:
        """归一化文件名/任务文本，便于做弱匹配。"""
        if not text:
            return ""
        s = text.lower()
        s = s.replace("tea_", "").replace("ref_", "")
        s = re.sub(r"[\s_\-—（）()【】\[\]，,。:：'\"“”]+", "", s)
        return s

    @staticmethod
    def _extract_filename_mentions(text: str) -> list[str]:
        """从任务描述中提取可能出现的文件名（如 ROM内容.txt）。"""
        if not text:
            return []
        mentions = set()
        quoted = re.findall(
            r"[\"“”'‘’]([^\"“”'‘’]+?\.(?:txt|md|pdf|docx))[\"“”'‘’]",
            text,
            flags=re.IGNORECASE,
        )
        mentions.update(m.strip() for m in quoted if m.strip())

        bare = re.findall(
            r"([\w\u4e00-\u9fa5\-\s]+?\.(?:txt|md|pdf|docx))",
            text,
            flags=re.IGNORECASE,
        )
        mentions.update(m.strip() for m in bare if m.strip())
        return list(mentions)

    def _match_instruction_docs_for_task(
        self, task: TaskRecord, instruction_docs: List[Path]
    ) -> list[str]:
        """按任务语义给 instruction docs 打分，返回最相关文件路径列表。"""
        if not instruction_docs:
            return []

        corpus = "\n".join(
            [
                task.task_name or "",
                task.analysis_raw or "",
                task.section_text or "",
                task.target_subcircuit or "",
            ]
        )
        corpus_norm = self._normalize_name(corpus)
        mentions = self._extract_filename_mentions(corpus)
        mention_norms = [self._normalize_name(x) for x in mentions if x]

        scored: list[tuple[int, Path]] = []
        for doc in instruction_docs:
            name = doc.name
            name_norm = self._normalize_name(name)
            score = 0
            direct_hit = False

            # 文件名直接提及优先级最高
            for m in mention_norms:
                if not m:
                    continue
                if m in name_norm or name_norm in m:
                    score += 20
                    direct_hit = True

            # 子电路与关键词匹配
            if task.target_subcircuit:
                t_norm = self._normalize_name(task.target_subcircuit)
                if t_norm and (t_norm in name_norm or name_norm in t_norm):
                    score += 8

            keywords = ["rom", "ram", "cache", "mips", "存储器", "地址", "期望值"]
            for kw in keywords:
                if kw in corpus_norm and kw in name_norm:
                    score += 3

            # 通用弱匹配：任务文本里出现文件名（去后缀）
            stem_norm = self._normalize_name(doc.stem)
            if stem_norm and stem_norm in corpus_norm:
                score += 6

            if direct_hit or score >= 6:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        # 仅保留前 5 个，避免 prompt 过长
        return [str(p) for _, p in scored[:5]]

    def _annotate_task_with_docs(self, task: TaskRecord):
        """把任务关联文件显式标记到 analysis_raw，便于下游明确使用目标文件。"""
        if not task.task_instruction_docs:
            return
        names = ", ".join(Path(p).name for p in task.task_instruction_docs)
        marker = f"任务参考文件: {names}"
        if marker not in task.analysis_raw:
            task.analysis_raw = (task.analysis_raw.rstrip() + "\n" + marker).strip()

    def _categorize_workspace_files(self) -> Dict[str, List[Path]]:
        """
        对工作区内已平铺（且带 TEA_/REF_ 前缀）的文件进行分类。
        """
        files = list(self.workspace_dir.iterdir())
        categories = {
            "instruction_pdf": [],
            "instruction_text": [],
            "report_template": [],
            "teacher_circuits": [],
            "reference_circuits": [],
            "reference_reports": [],
            "other": [],
        }

        for f in files:
            name = f.name
            suffix = f.suffix.lower()
            is_ref = name.startswith("REF_")
            is_tea = name.startswith("TEA_")

            # 去掉前缀进行关键字判断
            pure_name = name[4:] if (is_ref or is_tea) else name

            if suffix == ".pdf":
                if is_tea and ("实验" in pure_name or "指导" in pure_name):
                    categories["instruction_pdf"].append(f)
                elif is_ref:
                    categories["reference_reports"].append(f)
                else:
                    categories["other"].append(f)
            elif suffix == ".docx":
                if is_tea and (
                    "报告" in pure_name or "模板" in pure_name or "样本" in pure_name
                ):
                    categories["report_template"].append(f)
                elif is_ref:
                    categories["reference_reports"].append(f)
                else:
                    categories["other"].append(f)
            elif suffix in (".txt", ".md"):
                if is_tea:
                    categories["instruction_text"].append(f)
                elif is_ref:
                    categories["reference_reports"].append(f)
                else:
                    categories["other"].append(f)
            elif suffix == ".circ":
                if is_ref:
                    categories["reference_circuits"].append(f)
                else:
                    categories["teacher_circuits"].append(f)
            else:
                categories["other"].append(f)

        return categories

    async def _parse_docx_outline_from_dir(self, input_dir: Path) -> List[Dict]:
        """从 input_dir 中找到第一个 *.docx 并通过 LLM 解析大纲。

        无 docx 时返回空列表（调用方以"未提供大纲"的分支处理，属于合法输入）；
        docx 存在但 LLM 解析失败属于不可恢复错误，直接抛出。
        """
        from ..utils.docx_outline import llm_parse_outline

        docx_files = sorted(input_dir.glob("*.docx"))
        if not docx_files:
            return []
        paths = [str(f) for f in docx_files]
        try:
            return await llm_parse_outline(
                paths, self.extractor.client, self.extractor.model_id
            )
        except Exception as e:
            raise RuntimeError(
                f"解析指导书大纲失败: {e}（文件: {[Path(p).name for p in paths]}）"
            ) from e

    async def _llm_judge_uncovered_groups(
        self, outline: List[Dict], cached_result: "ParsingResult"
    ) -> List[Dict]:
        """[LLM 判定] 基于语义判断大纲哪些 group 未被缓存任务覆盖。

        策略（按 task_type 分桶独立判定，避免跨类型误覆盖）：
        - 把 outline 的每个 (section, group) 展平成候选条目，编号 cand_id；
          章节号硬映射到 task_type：3.1→verification / 3.2→design / 3.3→challenge。
        - 对每种 task_type 单独跑一次 LLM：只拿同类型的候选 vs 同类型的缓存任务对比，
          LLM 不会被其他类型的噪声干扰。
        - 3.3 挑战题额外允许被 task_type=design 且 source_circ 指向『挑战实验.circ』的
          缓存任务覆盖（历史数据兼容）。
        - 3.1/3.2：逐 group 收集未覆盖者。
        - 3.3：整节任一 group 覆盖则整节跳过；否则折叠为"任选1组"合成 group。
        - LLM 失败时持续重试，直到得到合法结果（不做关键词兜底）。
        """
        if not outline:
            return []

        _SECTION_TT = {"3.1": "verification", "3.2": "design", "3.3": "challenge"}

        # 展平候选并按 task_type 分桶
        cand_by_type: Dict[str, List[Dict]] = {
            "verification": [],
            "design": [],
            "challenge": [],
        }
        for sec in outline:
            sec_num = str(sec.get("num", ""))
            sec_title = str(sec.get("title", ""))
            tt = _SECTION_TT.get(sec_num)
            if tt is None:
                continue
            for idx, g in enumerate(sec.get("groups", []), start=1):
                desc = str(g.get("description", "")).strip()
                if not desc:
                    continue
                cand_by_type[tt].append(
                    {
                        "cand_id": f"{sec_num}-{idx}",
                        "section_num": sec_num,
                        "section_title": sec_title,
                        "group_index": idx,
                        "description": desc,
                        "_group_obj": g,
                    }
                )

        if not any(cand_by_type.values()):
            return []

        # 缓存任务按 task_type 分桶
        all_tasks = cached_result.verification_tasks + cached_result.design_tasks
        tasks_by_type: Dict[str, List] = {
            "verification": [],
            "design": [],
            "challenge": [],
        }
        for t in all_tasks:
            tt = (t.task_type or "").strip()
            if tt in tasks_by_type:
                tasks_by_type[tt].append(t)

        # 3.3 挑战：允许被 task_type=design 且 source_circ 含『挑战实验.circ』的任务覆盖
        challenge_extra_tasks = [
            t
            for t in tasks_by_type["design"]
            if any("挑战实验" in (s or "") for s in (t.source_circ or []))
        ]

        covered_map: Dict[str, bool] = {}

        for tt in ("verification", "design", "challenge"):
            candidates = cand_by_type[tt]
            if not candidates:
                continue

            tasks_for_type = list(tasks_by_type[tt])
            if tt == "challenge":
                tasks_for_type.extend(challenge_extra_tasks)

            # 该类型缓存任务为空 → 整桶未覆盖，跳过 LLM
            if not tasks_for_type:
                for c in candidates:
                    covered_map[c["cand_id"]] = False
                continue

            partial = await self._llm_judge_single_type(tt, candidates, tasks_for_type)
            covered_map.update(partial)

        # 按章节重组
        result: List[Dict] = []
        for sec in outline:
            sec_num = str(sec.get("num", ""))
            sec_title = str(sec.get("title", ""))
            groups = list(sec.get("groups", []))

            # 3.3：整节任一命中即跳过；全未命中则折叠为"任选1组"
            if sec_num.startswith("3.3"):
                any_hit = False
                for idx, _g in enumerate(groups, start=1):
                    if covered_map.get(f"{sec_num}-{idx}", False):
                        any_hit = True
                        break
                if not any_hit:
                    combined_desc_lines: List[str] = []
                    for g in groups:
                        d = str(g.get("description", "")).strip()
                        if d:
                            combined_desc_lines.append(d)
                    head = combined_desc_lines[0] if combined_desc_lines else ""
                    if "任选1组" not in head:
                        combined_desc_lines.insert(
                            0, f"{sec_title}：请从以下任选1组完成。"
                        )
                    synthetic_group = {
                        "index": 1,
                        "description": "\n".join(combined_desc_lines),
                        "questions": [],
                    }
                    result.append(
                        {
                            "num": sec_num,
                            "title": sec_title,
                            "groups": [synthetic_group],
                        }
                    )
                continue

            # 3.1/3.2：逐 group 收集未覆盖
            missed = []
            for idx, g in enumerate(groups, start=1):
                if not covered_map.get(f"{sec_num}-{idx}", False):
                    missed.append(g)
            if missed:
                result.append({"num": sec_num, "title": sec_title, "groups": missed})

        return result

    async def _llm_judge_single_type(
        self,
        task_type: str,
        candidates: List[Dict],
        tasks: List,
    ) -> Dict[str, bool]:
        """对单一 task_type 做一次覆盖度判定，返回 {cand_id: covered}。"""
        cand_lines = [
            f'- cand_id="{c["cand_id"]}" 章节={c["section_num"]} {c["section_title"]}\n'
            f'  组描述: {c["description"][:600]}'
            for c in candidates
        ]
        task_lines = []
        for t in tasks:
            nm = (t.task_name or "").strip()
            src = ",".join(t.source_circ or []) or "(无)"
            tgt = (t.target_subcircuit or "").strip() or "(无)"
            ar = (t.analysis_raw or "").strip()
            task_lines.append(
                f'- "{nm}" | source_circ={src} | target_subcircuit={tgt} | {ar[:120]}'
            )

        expected_ids = [c["cand_id"] for c in candidates]
        type_hint_map = {
            "verification": "本轮只判定【验证性实验】（章节 3.1）",
            "design": "本轮只判定【设计性实验】（章节 3.2）",
            "challenge": "本轮只判定【挑战性实验】（章节 3.3）",
        }
        extra_rule = ""
        if task_type == "challenge":
            extra_rule = (
                "- 挑战性实验的关键识别信号是 source_circ 文件名含『挑战实验』或任务"
                "主题明确针对题面要求的挑战组（例如 cache 挑战实验.circ、4 个 cache 块"
                "/16 个 cache 块等不同于验证实验的规模变体）。\n"
                "- 如果缓存中【全部】任务的 source_circ 都是『xxx 验证实验.circ』或"
                "『xxx 设计实验.circ』，没有任何任务指向『挑战实验』文件名或题面规模，"
                "则全部候选 covered=false。\n"
            )

        prompt = (
            f"你是实验任务覆盖度判定助手。{type_hint_map[task_type]}。\n"
            "给定：（A）同类型的指导书候选实验组；（B）同类型的缓存实验任务。\n"
            "请对每个候选组判断：缓存中是否已有针对该组的实验任务。\n\n"
            "【判定口径】\n"
            "- 核心依据：候选组描述中的电路名称 / 设计文件名 / 规模（如 cache 块数、"
            "寄存器个数）是否与某条缓存任务的 target_subcircuit / source_circ / "
            "task_name / analysis_raw 一致或是子集。\n"
            "- 一致或子集 → covered=true；否则 covered=false。\n"
            "- 不得以『主题近似』兜底；若找不到明确对应的核心电路/文件名/规模，"
            "必须 covered=false。\n"
            f"{extra_rule}\n"
            "【强制输出】必须对下列全部 cand_id 作出判断，逐一出现在 results 中：\n"
            f"{expected_ids}\n\n"
            "仅输出 JSON：\n"
            '{"results":[{"cand_id":"x.y-z","covered":true|false,"reason":"简短理由"}]}\n\n'
            "【A. 候选实验组】\n"
            + "\n".join(cand_lines)
            + "\n\n【B. 缓存任务】\n"
            + "\n".join(task_lines)
        )

        max_attempts = 5
        last_err: Optional[str] = None
        for attempt in range(1, max_attempts + 1):
            try:
                json_str = await self.extractor._call_with_json_retry(
                    prompt,
                    context_label=(
                        f"Cache 覆盖度判定[{task_type}] "
                        f"(attempt {attempt}/{max_attempts})"
                    ),
                    max_json_retries=2,
                )
                if not json_str:
                    last_err = "LLM 未返回合法 JSON"
                    print(
                        f"[Cache] 覆盖度判定[{task_type}] 第 "
                        f"{attempt}/{max_attempts} 次未获得合法 JSON，重试..."
                    )
                    continue
                data = json.loads(json_str)
                results = data.get("results", []) if isinstance(data, dict) else []
                parsed: Dict[str, bool] = {}
                for item in results:
                    cid = str(item.get("cand_id", "")).strip()
                    if cid:
                        parsed[cid] = bool(item.get("covered", False))
                missing = [cid for cid in expected_ids if cid not in parsed]
                if missing:
                    last_err = f"缺少 cand_id 判定: {missing}"
                    print(
                        f"[Cache] 覆盖度判定[{task_type}] 第 "
                        f"{attempt}/{max_attempts} 次结果缺失 "
                        f"{len(missing)} 项，重试..."
                    )
                    continue
                return parsed
            except Exception as e:
                last_err = str(e)
                print(
                    f"[Cache] 覆盖度判定[{task_type}] 第 "
                    f"{attempt}/{max_attempts} 次异常：{e}，重试..."
                )

        raise RuntimeError(
            f"LLM 覆盖度判定[{task_type}] 连续失败 {max_attempts} 次"
            f"（最后错误：{last_err}）"
        )

    @staticmethod
    def _format_uncovered(uncovered: List[Dict]) -> List[str]:
        """把 _llm_judge_uncovered_groups 的结果格式化为可读字符串列表（用于日志）。
        按章节分段输出；3.3 章节会额外标注"任选1组"语义，避免误以为要全做。
        """
        lines: List[str] = []
        for item in uncovered:
            sec_num = str(item.get("num", ""))
            sec_title = str(item.get("title", ""))
            groups = list(item.get("groups", []))

            if sec_num.startswith("3.3"):
                lines.append(
                    f"[章节 {sec_num} {sec_title}] 挑战性实验，将由 LLM 结合 REF 报告"
                    f"在以下候选中【任选1组】完成（共 {len(groups)} 条候选描述）："
                )
            else:
                lines.append(
                    f"[章节 {sec_num} {sec_title}] 共 {len(groups)} 个实验组未覆盖，"
                    f"将逐一补全："
                )

            for g in groups:
                desc = str(g.get("description", "")).strip()
                idx = g.get("index", "?")
                # 多行 description 缩进展示，不截断
                desc_lines = [ln.rstrip() for ln in desc.splitlines() if ln.strip()]
                if not desc_lines:
                    desc_lines = ["(无描述)"]
                lines.append(f"  ({sec_num}-{idx}) {desc_lines[0]}")
                for extra in desc_lines[1:]:
                    lines.append(f"        {extra}")
        return lines

    @staticmethod
    def _summarize_existing_tasks_for_phase1(cached: "ParsingResult") -> str:
        """把缓存里所有任务的关键字段摘要成 LLM 友好的列表，
        喂给 phase1 用于增量补全模式的去重。"""
        all_tasks = cached.verification_tasks + cached.design_tasks
        if not all_tasks:
            return ""
        lines: List[str] = []
        for t in all_tasks:
            tt = (t.task_type or "").strip()
            nm = (t.task_name or "").strip()
            tgt = (t.target_subcircuit or "").strip() or "(无)"
            src = ",".join(t.source_circ or []) or "(无)"
            ar = (
                (t.analysis_raw or "").strip().splitlines()[0] if t.analysis_raw else ""
            )
            lines.append(
                f'- [{tt}] "{nm}" | target_subcircuit={tgt} | '
                f"source_circ={src} | {ar[:120]}"
            )
        return "\n".join(lines)

    async def run(self, input_dir: Path) -> ParsingResult:
        """执行内容解析全流程"""
        if not input_dir.exists():
            raise FileNotFoundError(f"内容解析失败：输入目录不存在 -> {input_dir}")

        # 0-pre. 解析 DOCX 大纲（用于缓存校验 & Phase1 引导）
        outline = await self._parse_docx_outline_from_dir(input_dir)
        if outline:
            total_groups = sum(len(s.get("groups", [])) for s in outline)
            print(
                f"[DocxOutline] 解析大纲：{len(outline)} 个章节，共 {total_groups} 个实验组"
            )
        else:
            print("[DocxOutline] 未找到可用大纲，将依赖指导书全文解析")

        # --- [Cache Verification] ---
        if self.cache:
            cached_result = self.cache.load_parsing_result()
            if cached_result:
                cache_mtime = self.cache.parsing_file.stat().st_mtime
                is_outdated = any(
                    f.is_file() and f.stat().st_mtime > cache_mtime
                    for f in input_dir.glob("*")
                )

                if not is_outdated:
                    print("[Cache] 正在通过 LLM 语义判定大纲覆盖度...")
                    uncovered = await self._llm_judge_uncovered_groups(
                        outline, cached_result
                    )
                    if not uncovered:
                        print("[Cache] 大纲覆盖度校验通过，直接使用缓存。")
                        return cached_result

                    display = self._format_uncovered(uncovered)
                    print(
                        f"[Cache] 以下实验组缓存未覆盖，将增量补全（已命中部分保留）:\n"
                        + "\n".join(f"  - {d}" for d in display)
                    )
                    # 增量解析：只对未命中的 groups 跑流水线，再合并进缓存
                    partial = await self._run_parse_pipeline(
                        input_dir,
                        target_outline=uncovered,
                        existing_cached=cached_result,
                    )
                    merged = self._merge_with_old_cache(partial, cached_result)
                    self.cache.save_parsing_result(merged)
                    return merged
                else:
                    print("[Cache] 输入文件已更新，缓存失效，执行全量解析。")
        # -----------------------------

        # 全量解析
        result = await self._run_parse_pipeline(input_dir, target_outline=outline)

        if self.cache:
            old = self.cache.load_parsing_result()
            if old:
                result = self._merge_with_old_cache(result, old)
            self.cache.save_parsing_result(result)

        return result

    async def _run_parse_pipeline(
        self,
        input_dir: Path,
        target_outline: Optional[List[Dict]] = None,
        existing_cached: Optional["ParsingResult"] = None,
    ) -> ParsingResult:
        """解析流水线核心（不含缓存读写）。

        target_outline: 若非 None，则 phase1 只生成该大纲范围内的实验（增量补全用）；
                        若为 None，则生成全部实验。
        existing_cached: 增量补全时把已有缓存任务摘要喂给 phase1，避免重复生成。
        """
        # 0. 清理工作区，确保是幂等的全新运行
        if self.workspace_dir.exists():
            shutil.rmtree(self.workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # 1. 扫描输入目录，优先提取任务文本
        raw_text = ""
        input_files = list(input_dir.iterdir())
        for item in input_files:
            suffix = item.suffix.lower()
            if suffix == ".pdf":
                raw_text += self.extractor.extract_text_from_pdf(item)
            elif suffix == ".docx":
                raw_text += self.extractor.extract_text_from_docx(item)
            elif suffix in (".txt", ".md"):
                raw_text += item.read_text(encoding="utf-8", errors="ignore") + "\n"

        # 2. 扫描并解压/拷贝所有文件到工作区
        ref_pattern = re.compile(r"^\d+[\+\s\-_]+[\u4e00-\u9fa5]+")
        for item in input_files:
            suffix = item.suffix.lower()
            if suffix == ".zip":
                self.decompressor.unzip_recursive(item)
            elif suffix in (".circ", ".pdf", ".docx", ".txt", ".md"):
                is_ref = ref_pattern.search(item.name) is not None
                prefix = "REF_" if is_ref else "TEA_"
                shutil.copy(item, self.workspace_dir / f"{prefix}{item.name}")

        # 3. 分类工作区文件
        cat = self._categorize_workspace_files()

        # 兜底读取解压后的指导书
        if not raw_text:
            for f in cat["instruction_pdf"]:
                raw_text += self.extractor.extract_text_from_pdf(f)
            for f in cat["instruction_text"]:
                raw_text += f.read_text(encoding="utf-8", errors="ignore") + "\n"

        # 4. 准备文件清单
        tea_names = [f.name for f in cat["teacher_circuits"]]
        ref_names = [f.name for f in cat["reference_circuits"]]

        # =========================================================
        # 阶段一：高层次分类
        # target_outline 为 None 时覆盖全部；非 None 时只覆盖列出的 groups
        # =========================================================
        p1_path = Path("prompts/parsing/phase1_classify.txt")
        p1_template = p1_path.read_text(encoding="utf-8") if p1_path.exists() else ""
        is_partial = target_outline is not None
        label = "增量补全" if is_partial else "全量"
        print(f"[阶段一] 正在{label}分类实验模块...")
        existing_summary = (
            self._summarize_existing_tasks_for_phase1(existing_cached)
            if is_partial and existing_cached
            else ""
        )
        phase1_data = await self.extractor.phase1_classify(
            raw_text,
            p1_template,
            tea_names,
            ref_names,
            outline=target_outline,
            partial_reparse=is_partial,
            existing_tasks_summary=existing_summary,
        )
        experiments = phase1_data.get("experiments", [])
        reference_report_text = self._collect_reference_report_text(
            cat["reference_reports"]
        )
        experiments = await self._align_challenge_experiments_with_reference(
            experiments, reference_report_text
        )
        print(f"   → 识别出 {len(experiments)} 个实验模块")

        # =========================================================
        # 阶段二：仅对验证性实验进行原子级拆解
        # =========================================================
        p2_path = Path("prompts/parsing/phase2_verify_detail.txt")
        p2_template = p2_path.read_text(encoding="utf-8") if p2_path.exists() else ""

        all_tasks: List[TaskRecord] = []
        per_task_instruction_pool = cat["instruction_text"] + cat["instruction_pdf"]

        for exp in experiments:
            exp_name = exp.get("name", "未命名")
            exp_type = exp.get("task_type", "verification")
            exp_section = exp.get("section_text", "")
            exp_desc = exp.get("description", "")
            s_name = exp.get("matched_source_circ")
            r_name = exp.get("matched_reference_circ")
            source_path = (
                [str(self.workspace_dir / s_name)]
                if s_name and s_name != "null"
                else []
            )
            ref_path = (
                str(self.workspace_dir / r_name)
                if r_name and r_name != "null"
                else None
            )

            if exp_type == "verification":
                print(f"   [阶段二] 细化验证实验: {exp_name}")
                sub_items = await self.extractor.phase2_detail_verify(exp, p2_template)
                print(f"      → 拆解出 {len(sub_items)} 条测试用例")
                problem_answers = self._extract_problem_answers(exp_section)
                for sub in sub_items:
                    task = TaskRecord(
                        task_name=sub.get("task_name", exp_name),
                        task_type="verification",
                        analysis_raw=sub.get("description", ""),
                        section_text=exp_section,
                        target_subcircuit=exp.get("target_subcircuit"),
                        experiment_objective=exp_desc,
                        experiment_environment="",
                        thinking_questions=[],
                        problem_answers=problem_answers.copy(),
                    )
                    task.source_circ = source_path
                    task.task_instruction_docs = self._match_instruction_docs_for_task(
                        task, per_task_instruction_pool
                    )
                    self._annotate_task_with_docs(task)
                    all_tasks.append(task)

            else:
                problem_answers = self._extract_problem_answers(exp_section)
                task = TaskRecord(
                    task_name=exp_name,
                    task_type=exp_type,
                    analysis_raw=exp_desc,
                    section_text=exp_section,
                    target_subcircuit=exp.get("target_subcircuit"),
                    experiment_objective=exp.get("description", ""),
                    experiment_environment="",
                    thinking_questions=[],
                    problem_answers=problem_answers,
                )
                task.source_circ = source_path
                task.reference_circ = ref_path
                task.task_instruction_docs = self._match_instruction_docs_for_task(
                    task, per_task_instruction_pool
                )
                self._annotate_task_with_docs(task)
                all_tasks.append(task)

        # 阶段三：迭代精细化（验证实验原子拆分）
        all_tasks = await self._refine_tasks_iteratively(all_tasks)

        result = ParsingResult()
        result.verification_tasks = [
            t for t in all_tasks if t.task_type == "verification"
        ]
        result.design_tasks = [
            t for t in all_tasks if t.task_type in ("design", "challenge")
        ]
        result.reference_reports = [str(f) for f in cat["reference_reports"]]
        result.instruction_docs = (
            [str(f) for f in cat["instruction_pdf"]]
            + [str(f) for f in cat["instruction_text"]]
            + [str(f) for f in cat["report_template"]]
        )
        result.raw_experiments = experiments
        return result

    async def _refine_tasks_iteratively(
        self, initial_tasks: List[TaskRecord]
    ) -> List[TaskRecord]:
        """
        阶段三：迭代精细化拆解。
        对验证性任务进行递归检查与拆分，严格限制在 3 轮内。
        """
        p3_check_path = Path("prompts/parsing/phase3_check.txt")
        p3_split_path = Path("prompts/parsing/phase3_split.txt")
        if not p3_check_path.exists() or not p3_split_path.exists():
            return initial_tasks

        check_prompt = p3_check_path.read_text(encoding="utf-8")
        split_prompt = p3_split_path.read_text(encoding="utf-8")
        model_pro = self.config.gemini.model_pro

        from collections import deque

        # (TaskRecord, current_round)
        queue = deque([(t, 0) for t in initial_tasks])
        final_tasks = []

        print(f" (阶段三) 启动迭代任务精细化 (Max 3 rounds)...")

        while queue:
            task, rounds = queue.popleft()

            # 仅对验证任务进行拆分
            if task.task_type != "verification" or rounds >= 3:
                final_tasks.append(task)
                continue

            # 1. 检查是否需要拆分 (Flash)
            can_split = await self.extractor.phase3_check_subdivision(
                task.analysis_raw, check_prompt
            )

            if can_split:
                print(f"      [Round {rounds+1}] 发现可拆分任务: {task.task_name}")
                # 2. 执行拆分 (Pro)
                sub_data_list = await self.extractor.phase3_split_task(
                    task.analysis_raw, task.section_text, split_prompt, model_pro
                )
                if not sub_data_list:
                    raise RuntimeError(
                        f"[阶段三/Split] 任务 {task.task_name!r} 标记为可拆分但返回空列表"
                    )
                print(f"      → 成功拆分为 {len(sub_data_list)} 条更细原子任务")
                for sub in sub_data_list:
                    new_task = task.model_copy()
                    new_task.task_name = sub.get("task_name", task.task_name)
                    new_task.analysis_raw = sub.get("description", "")
                    # 核心元数据继承自父任务
                    queue.append((new_task, rounds + 1))
            else:
                # 无需拆分
                final_tasks.append(task)

        print(f"   → 最终生成 {len(final_tasks)} 条精细化任务")
        return final_tasks

    def _merge_with_old_cache(
        self, new_result: ParsingResult, old_result: ParsingResult
    ) -> ParsingResult:
        """增量合并：新解析结果 + 旧缓存中仍有效的任务。

        合并规则：
        1. 以任务名为 key，新解析的任务**优先**（覆盖同名旧任务）。
        2. 旧任务保留条件：其 source_circ 中**至少一个**文件仍存在于 workspace，
           且在新解析结果中**没有同名任务**。
        3. 旧任务丢弃条件：source_circ 全部文件不存在（源电路已被清除），
           视为"完全不相关"直接丢弃。
        """

        def _src_still_exists(task: TaskRecord) -> bool:
            if not task.source_circ:
                return False
            return any(Path(p).exists() for p in task.source_circ)

        def _merge_list(
            new_tasks: List[TaskRecord], old_tasks: List[TaskRecord]
        ) -> List[TaskRecord]:
            new_by_name: Dict[str, TaskRecord] = {
                t.task_name: t for t in new_tasks if t.task_name
            }
            merged: Dict[str, TaskRecord] = dict(new_by_name)
            kept = 0
            dropped = 0
            for old_t in old_tasks:
                name = old_t.task_name
                if name in merged:
                    continue  # 新结果已有同名任务，跳过旧任务
                if _src_still_exists(old_t):
                    merged[name] = old_t
                    kept += 1
                else:
                    dropped += 1
            if kept or dropped:
                print(
                    f"[ContentParsing] 增量合并：保留旧任务 {kept} 条，"
                    f"丢弃源文件缺失旧任务 {dropped} 条。"
                )
            return list(merged.values())

        new_result.verification_tasks = _merge_list(
            new_result.verification_tasks, old_result.verification_tasks
        )
        new_result.design_tasks = _merge_list(
            new_result.design_tasks, old_result.design_tasks
        )
        return new_result

    def _extract_problem_answers(self, text: str) -> List[dict[str, str]]:
        """从实验原文中提取“回答问题/思考题”列表，返回 problem/answer 结构。"""
        if not text:
            return []

        lines = [line.strip(" \t•") for line in text.splitlines()]
        problems: List[str] = []
        collecting = False

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            if re.search(r"^(回答问题|思考题|问题[:：]?)", line):
                collecting = True
                continue

            if collecting:
                if re.match(r"^(\d+[\)）\.]|[①②③④⑤⑥⑦⑧⑨⑩])", line):
                    problem = re.sub(
                        r"^(\d+[\)）\.]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", "", line
                    ).strip()
                    if problem:
                        problems.append(problem)
                    continue

                if line.startswith("•") or re.search(
                    r"(输入|输出|验证|思路|请同学们)", line
                ):
                    break

        seen = set()
        result: List[dict[str, str]] = []
        for problem in problems:
            if problem in seen:
                continue
            seen.add(problem)
            result.append({"problem": problem, "answer": ""})
        return result

    def _collect_reference_report_text(self, report_paths: List[Path]) -> str:
        """汇总参考报告文本，用于挑战实验选组对齐。"""
        chunks: List[str] = []
        used: List[str] = []
        for path in report_paths:
            try:
                if path.suffix.lower() == ".pdf":
                    chunks.append(self.extractor.extract_text_from_pdf(path))
                elif path.suffix.lower() == ".docx":
                    chunks.append(self.extractor.extract_text_from_docx(path))
                elif path.suffix.lower() in (".txt", ".md"):
                    chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
                else:
                    continue
                used.append(path.name)
            except Exception as e:
                print(f"[挑战实验对齐] 读取参考报告 {path.name} 失败: {e}")
        if used:
            total_chars = sum(len(c) for c in chunks)
            print(
                f"[挑战实验对齐] 加载 REF 参考报告 {len(used)} 份（共 {total_chars} 字）: "
                + ", ".join(used)
            )
        else:
            print("[挑战实验对齐] 未发现 REF 参考报告，只能按参考电路文件名选组")
        return "\n".join(chunks)

    async def _align_challenge_experiments_with_reference(
        self, experiments: List[dict], reference_report_text: str
    ) -> List[dict]:
        """若挑战性实验存在“任选1组”，则依据 REF 报告和 REF 电路对齐到同一组。

        修复：phase1 可能把 3.3 的"第1组/第2组/.."拆成了多条独立 challenge 实验，
        每条的 section_text 只有自己一组、不含"任选1组"字样，对齐会被跳过。
        因此先按 matched_reference_circ 把多条 challenge 聚合成一条（section_text
        拼接并补上"任选1组"前缀），再走原对齐流程。
        """
        # 1) 先聚合：按 reference_circ 合并同属一次挑战实验的多条
        challenge_idxs = [
            i for i, e in enumerate(experiments) if e.get("task_type") == "challenge"
        ]
        if len(challenge_idxs) >= 2:
            buckets: Dict[str, List[int]] = {}
            for i in challenge_idxs:
                key = str(experiments[i].get("matched_reference_circ") or "")
                buckets.setdefault(key, []).append(i)

            merged_experiments: List[dict] = []
            consumed: set = set()
            for i, exp in enumerate(experiments):
                if i in consumed:
                    continue
                if exp.get("task_type") != "challenge":
                    merged_experiments.append(exp)
                    continue
                ref_key = str(exp.get("matched_reference_circ") or "")
                group_idxs = buckets.get(ref_key, [i])
                if len(group_idxs) <= 1:
                    merged_experiments.append(exp)
                    continue
                # 合并
                parts = [experiments[j].get("section_text", "") for j in group_idxs]
                combined_section = "任选1组完成以下挑战性实验：\n" + "\n".join(
                    p.strip() for p in parts if p.strip()
                )
                combined_desc = "；".join(
                    experiments[j].get("description", "") for j in group_idxs
                )
                merged_exp = dict(exp)
                merged_exp["section_text"] = combined_section
                merged_exp["description"] = combined_desc
                merged_exp["name"] = (
                    exp.get("name", "").split("第", 1)[0].strip().rstrip(":：")
                    or "挑战性实验"
                )
                merged_experiments.append(merged_exp)
                consumed.update(group_idxs)
            experiments = merged_experiments

        aligned: List[dict] = []
        for exp in experiments:
            if exp.get("task_type") != "challenge":
                aligned.append(exp)
                continue

            section_text = exp.get("section_text", "")
            groups = self._split_challenge_groups(section_text)
            if len(groups) <= 1:
                aligned.append(exp)
                continue

            selected = await self._select_challenge_group_with_reference(
                exp, groups, reference_report_text
            )
            if not selected:
                aligned.append(exp)
                continue

            prefix = section_text.split(selected["label"], 1)[0].rstrip()
            selected_section_text = (
                prefix
                + "\n"
                + f"• 已根据参考电路与参考报告对齐，选择{selected['label']}。\n"
                + selected["text"].strip()
            )

            updated = dict(exp)
            updated["section_text"] = selected_section_text
            updated["description"] = (
                exp.get("description", "")
                + f" 已根据参考资料对齐为{selected['label']}。"
            ).strip()
            aligned.append(updated)
            print(
                f"[挑战实验对齐] {exp.get('name', '未命名挑战实验')} -> 选择 {selected['label']}"
            )

        return aligned

    def _split_challenge_groups(self, section_text: str) -> List[dict]:
        """从 section_text 中切出“第1组/第2组...”候选组。"""
        if not section_text or "任选1组" not in section_text:
            return []

        pattern = re.compile(
            r"(第[0-9一二三四五六七八九十]+组[:：].*?)(?=第[0-9一二三四五六七八九十]+组[:：]|$)",
            re.DOTALL,
        )
        groups: List[dict] = []
        for match in pattern.finditer(section_text):
            block = match.group(1).strip()
            label_match = re.match(r"(第[0-9一二三四五六七八九十]+组)", block)
            label = label_match.group(1) if label_match else f"第{len(groups)+1}组"
            groups.append({"label": label, "text": block})
        return groups

    async def _select_challenge_group_with_reference(
        self, exp: dict, groups: List[dict], reference_report_text: str
    ) -> Optional[dict]:
        """结合 REF 报告和 REF 电路，选择最匹配的挑战实验组。"""
        prompt_groups = "\n\n".join(
            [
                f"{idx+1}. {group['label']}\n{group['text']}"
                for idx, group in enumerate(groups)
            ]
        )
        ref_circ = exp.get("matched_reference_circ") or "无"
        prompt = (
            "你是实验内容对齐助手。给定一个挑战性实验的候选分组，以及参考电路文件名和参考报告内容，"
            "请判断参考资料实际实现/描述的是哪一组，并输出 JSON。\n"
            '输出格式：{"selected_index": 1, "reason": "..."}\n\n'
            f"实验名称: {exp.get('name', '')}\n"
            f"参考电路文件: {ref_circ}\n"
            f"候选分组:\n{prompt_groups}\n\n"
            f"参考报告摘录:\n{reference_report_text[:12000]}\n"
        )

        print(
            f"[挑战实验对齐] 准备选组：候选 {len(groups)} 组，REF 电路={ref_circ}，"
            f"REF 报告文本 {len(reference_report_text)} 字"
        )

        raw = await self.extractor._call_with_json_retry(prompt, "挑战实验选组")
        parsed = json.loads(raw)
        index = int(parsed.get("selected_index", 0)) - 1
        reason = str(parsed.get("reason", ""))
        if not (0 <= index < len(groups)):
            raise RuntimeError(
                f"[挑战实验对齐] LLM 返回非法的 selected_index="
                f"{parsed.get('selected_index')}（候选组数={len(groups)}）"
            )
        chosen = groups[index]
        print(
            f"[挑战实验对齐] LLM 选中 {chosen['label']}（selected_index={index+1}）\n"
            f"  依据REF电路: {ref_circ}\n"
            f"  选择理由: {reason}"
        )
        return chosen

    def _extract_challenge_group_tokens(self, text: str) -> List[str]:
        """从候选组文字中提取用于匹配的关键短语。"""
        tokens: List[str] = []
        patterns = [
            r"直接相联映射方式\s*cache电路\(\d+个cache块\)",
            r"全相联映射方式\s*cache电路\(\d+个cache块\)",
            r"\d+路组相联映射方式\s*cache电路\(\d+个cache块\)",
        ]
        compact = self._normalize_text(text)
        for pattern in patterns:
            for match in re.finditer(pattern, compact):
                tokens.append(match.group(0))
        if not tokens:
            tokens.append(compact)
        return tokens

    def _normalize_text(self, text: str) -> str:
        """简单归一化文本，方便跨文件名/报告做关键词匹配。"""
        compact = re.sub(r"\s+", "", text)
        compact = compact.replace("：", ":")
        compact = compact.replace("（", "(").replace("）", ")")
        return compact
