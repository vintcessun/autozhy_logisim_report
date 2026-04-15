import os
import shutil
import zipfile
import re
from pathlib import Path
import pdfplumber
from docx import Document
from typing import List, Dict, Optional, Tuple
from google import genai
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
            print(f"解压 {src_path.name} 失败: {e}")

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
            print(f"警告: 无法从 PDF {pdf_path.name} 提取文本: {e}")
        return text

    def extract_text_from_docx(self, docx_path: Path) -> str:
        """提取 DOCX 文本"""
        try:
            doc = Document(docx_path)
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            print(f"警告: 无法从 DOCX {docx_path.name} 提取文本: {e}")
            return ""

    @staticmethod
    def _extract_json(raw: str) -> str:
        """
        从 LLM 原始输出中提取合法的 JSON 字符串。
        处理以下情况：
          1. 被 ```json ... ``` 或 ``` ... ``` 包裹
          2. 前后有多余文字，用正则提取第一个完整的 { } 或 [ ] 块
          3. JSON 截断（不完整），返回空字符串留给调用方处理
        """
        import re as _re, json as _json

        text = raw.strip()

        # 1. 去除 markdown 代码块包裹
        md_fence = _re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, _re.DOTALL)
        if md_fence:
            text = md_fence.group(1).strip()

        # 2. 如果已经是合法 JSON，直接返回
        try:
            _json.loads(text)
            return text
        except Exception:
            pass

        # 3. 正则提取第一个完整的 JSON 对象 {...} 或数组 [...]
        for pattern in (r"(\{.*\})", r"(\[.*\])"):
            m = _re.search(pattern, text, _re.DOTALL)
            if m:
                candidate = m.group(1)
                try:
                    _json.loads(candidate)
                    return candidate
                except Exception:
                    pass

        return ""

    async def _call_with_json_retry(
        self,
        prompt: str,
        context_label: str,
        max_json_retries: int = 2,
        model_id: Optional[str] = None,
    ) -> str:
        """
        调用 LLM 并确保返回合法 JSON 字符串。
        若 JSON 解析失败，将错误内容发回 LLM 要求自修复，最多重试 max_json_retries 次。
        """
        import json as _json

        target_model = model_id or self.model_id
        response = await generate_content_with_tools(
            self.client,
            model=target_model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        raw = response.text

        for attempt in range(max_json_retries + 1):
            extracted = self._extract_json(raw)
            if extracted:
                try:
                    _json.loads(extracted)
                    return extracted
                except Exception:
                    pass

            if attempt < max_json_retries:
                print(
                    f"[JSON 自修复] {context_label} 第 {attempt+1} 次尝试失败，请求 LLM 修复..."
                )
                repair_prompt = (
                    "以下 JSON 输出不完整或格式有误，请原样修复并输出完整合法的 JSON，不要添加任何解释：\n\n"
                    + raw[:3000]
                )
                response = await generate_content_with_tools(
                    self.client,
                    model=target_model,
                    contents=repair_prompt,
                    config={"response_mime_type": "application/json"},
                )
                raw = response.text
            else:
                print(
                    f"[JSON 解析失败] {context_label} 达到最大重试次数，原始输出前 300 字：\n{raw[:300]}"
                )

        return ""

    async def phase1_classify(
        self,
        text: str,
        prompt_template: str,
        teacher_files: List[str],
        reference_files: List[str],
    ) -> dict:
        """阶段一：识别所有实验模块，分类并提取各模块对应的原文段落"""
        if not self.client:
            return {"experiments": []}

        prompt = prompt_template.replace(
            "{{teacher_files}}", "\n".join(teacher_files) if teacher_files else "无"
        )
        prompt = prompt.replace(
            "{{reference_files}}",
            "\n".join(reference_files) if reference_files else "无",
        )
        prompt = f"{prompt}\n\n待解析全文：\n{text}"

        import json as _json

        extracted = await self._call_with_json_retry(prompt, "阶段一分类")
        if not extracted:
            return {"experiments": []}
        try:
            return _json.loads(extracted)
        except Exception as e:
            print(f"阶段一最终解析失败: {e}")
            return {"experiments": []}

    async def phase2_detail_verify(
        self, experiment: dict, prompt_template: str
    ) -> List[dict]:
        """阶段二：仅针对验证性实验，将其 section_text 拆解为原子测试用例列表"""
        if not self.client:
            return []

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
        if not extracted:
            return []
        try:
            result = _json.loads(extracted)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return result.get("tasks", result.get("items", []))
            return []
        except Exception as e:
            print(f"{label} 最终解析失败: {e}")
            return []

    async def phase3_check_subdivision(
        self, task_desc: str, prompt_template: str
    ) -> bool:
        """阶段三检查：利用 Flash 判断是否可进一步拆分"""
        prompt = prompt_template.replace("{{task_description}}", task_desc)
        label = "[阶段三/Check]"

        import json as _json

        json_str = await self._call_with_json_retry(prompt, label)
        if not json_str:
            return False
        try:
            res = _json.loads(json_str)
            return res.get("can_be_subdivided", False)
        except:
            return False

    async def phase3_split_task(
        self, task_desc: str, section_text: str, prompt_template: str, model_pro: str
    ) -> List[dict]:
        """阶段三拆分：利用 Pro 将任务拆解为子任务"""
        prompt = prompt_template.replace("{{task_description}}", task_desc)
        prompt = prompt.replace("{{section_text}}", section_text)
        label = "[阶段三/Split]"

        import json as _json

        json_str = await self._call_with_json_retry(prompt, label, model_id=model_pro)
        if not json_str:
            return []
        try:
            res = _json.loads(json_str)
            if isinstance(res, list):
                return res
            return []
        except:
            return []

    # 保留旧方法兼容性（单元测试用）
    async def parse_tasks_with_llm(
        self,
        text: str,
        prompt_template: str,
        teacher_files: List[str],
        reference_files: List[str],
    ) -> dict:
        return await self.phase1_classify(
            text, prompt_template, teacher_files, reference_files
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

    async def run(self, input_dir: Path) -> ParsingResult:
        """执行内容解析全流程"""
        if not input_dir.exists():
            return ParsingResult()

        # --- [Cache Verification] ---
        if self.cache:
            cached_result = self.cache.load_parsing_result()
            if cached_result:
                # 1. 时间戳拦截
                cache_mtime = self.cache.parsing_file.stat().st_mtime
                input_files = list(input_dir.glob("*"))
                is_outdated = False
                for f in input_files:
                    if f.is_file() and f.stat().st_mtime > cache_mtime:
                        is_outdated = True
                        break

                if not is_outdated:
                    # 2. LLM 二次确认
                    print("[Cache] 正在通过 LLM 验证缓存一致性...")
                    if await self._confirm_cache_with_llm(input_dir, cached_result):
                        return cached_result
                    else:
                        print("[Cache] LLM 指示缓存不匹配，执行全新解析。")
                else:
                    print("[Cache] 输入文件已更新，缓存失效。")
        # -----------------------------

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
        # 阶段一：高层次分类——识别所有实验模块、类型、对应原文、电路关联
        # =========================================================
        p1_path = Path("prompts/parsing/phase1_classify.txt")
        p1_template = p1_path.read_text(encoding="utf-8") if p1_path.exists() else ""
        print("[阶段一] 正在分类实验模块...")
        phase1_data = await self.extractor.phase1_classify(
            raw_text, p1_template, tea_names, ref_names
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
            exp_section = exp.get("section_text", "")  # Phase 1 提取的原始段落
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
                # 阶段二：拆解为原子测试用例，同时把 section_text 喂入
                print(f"   [阶段二] 细化验证实验: {exp_name}")
                sub_items = await self.extractor.phase2_detail_verify(exp, p2_template)
                print(f"      → 拆解出 {len(sub_items)} 条测试用例")
                problem_answers = self._extract_problem_answers(exp_section)
                for sub in sub_items:
                    task = TaskRecord(
                        task_name=sub.get("task_name", exp_name),
                        task_type="verification",
                        analysis_raw=sub.get("description", ""),
                        section_text=exp_section,  # 保留原始段落供下游引用
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
                # 设计性/挑战性实验：保持单条，section_text 存入完整描述
                problem_answers = self._extract_problem_answers(exp_section)
                task = TaskRecord(
                    task_name=exp_name,
                    task_type=exp_type,
                    analysis_raw=exp_desc,
                    section_text=exp_section,  # 保留原始段落供下游引用
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
        result.raw_experiments = (
            experiments  # 阶段一全量数据（含 section_text），供下游全量参考
        )

        # 5. 保存结果到缓存
        if self.cache:
            self.cache.save_parsing_result(result)

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

                if sub_data_list:
                    print(f"      → 成功拆分为 {len(sub_data_list)} 条更细原子任务")
                    for sub in sub_data_list:
                        new_task = task.model_copy()
                        new_task.task_name = sub.get("task_name", task.task_name)
                        new_task.analysis_raw = sub.get("description", "")
                        # 核心元数据继承自父任务
                        queue.append((new_task, rounds + 1))
                else:
                    # 拆分失败，保留原样
                    final_tasks.append(task)
            else:
                # 无需拆分
                final_tasks.append(task)

        print(f"   → 最终生成 {len(final_tasks)} 条精细化任务")
        return final_tasks

    async def _confirm_cache_with_llm(
        self, input_dir: Path, cached_result: ParsingResult
    ) -> bool:
        """调用 LLM 判断当前输入是否与缓存的任务语义一致。"""
        # 提取当前输入文件的基本信息（文件名列表）
        input_files = [f.name for f in input_dir.glob("*") if f.is_file()]

        # 提取缓存中的任务信息
        cached_tasks = []
        for t in cached_result.verification_tasks + cached_result.design_tasks:
            cached_tasks.append(
                {
                    "task_name": t.task_name,
                    "type": t.task_type,
                    "objective": t.experiment_objective[:200],
                }
            )

        prompt = f"""
请判断当前的实验输入文件是否与之前的缓存任务列表一致。
如果大体一致（实验内容、目标、任务名称匹配），请返回使用缓存。

【当前输入文件列表】:
{input_files}

【历史缓存任务概览】:
{cached_tasks}

请以 JSON 格式回复：
{{"use_cache": true, "reason": "解释原因"}} 或者 {{"use_cache": false, "reason": "解释原因"}}
"""
        try:
            import json

            label = "[Cache 确认]"
            json_str = await self.extractor._call_with_json_retry(prompt, label)

            if not json_str:
                print(f"{label} LLM 未能返回合法 JSON，默认不使用缓存。")
                return False

            res_data = json.loads(json_str)
            return res_data.get("use_cache", False)
        except Exception as e:
            print(f"{label} 确认失败: {e}")
            return False

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
        for path in report_paths:
            try:
                if path.suffix.lower() == ".pdf":
                    chunks.append(self.extractor.extract_text_from_pdf(path))
                elif path.suffix.lower() == ".docx":
                    chunks.append(self.extractor.extract_text_from_docx(path))
                elif path.suffix.lower() in (".txt", ".md"):
                    chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception as e:
                print(f"[挑战实验对齐] 读取参考报告 {path.name} 失败: {e}")
        return "\n".join(chunks)

    async def _align_challenge_experiments_with_reference(
        self, experiments: List[dict], reference_report_text: str
    ) -> List[dict]:
        """若挑战性实验存在“任选1组”，则依据 REF 报告和 REF 电路对齐到同一组。"""
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
        prompt = (
            "你是实验内容对齐助手。给定一个挑战性实验的候选分组，以及参考电路文件名和参考报告内容，"
            "请判断参考资料实际实现/描述的是哪一组，并输出 JSON。\n"
            '输出格式：{"selected_index": 1, "reason": "..."}\n\n'
            f"实验名称: {exp.get('name', '')}\n"
            f"参考电路文件: {exp.get('matched_reference_circ') or '无'}\n"
            f"候选分组:\n{prompt_groups}\n\n"
            f"参考报告摘录:\n{reference_report_text[:12000]}\n"
        )

        try:
            raw = await self.extractor._call_with_json_retry(prompt, "挑战实验选组")
            if raw:
                parsed = json.loads(raw)
                index = int(parsed.get("selected_index", 0)) - 1
                if 0 <= index < len(groups):
                    return groups[index]
        except Exception as e:
            print(f"[挑战实验对齐] LLM 选组失败，改用规则兜底: {e}")

        return self._select_challenge_group_by_rules(exp, groups, reference_report_text)

    def _select_challenge_group_by_rules(
        self, exp: dict, groups: List[dict], reference_report_text: str
    ) -> Optional[dict]:
        """LLM 不可用时，基于关键词重合度进行挑战实验选组兜底。"""
        ref_text = self._normalize_text(
            (exp.get("matched_reference_circ") or "") + "\n" + reference_report_text
        )
        best_group = None
        best_score = -1

        for group in groups:
            tokens = self._extract_challenge_group_tokens(group["text"])
            score = 0
            for token in tokens:
                if token and token in ref_text:
                    score += max(1, len(token))
            if score > best_score:
                best_score = score
                best_group = group

        return best_group or (groups[0] if groups else None)

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
