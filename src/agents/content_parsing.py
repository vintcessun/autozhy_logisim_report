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
            with zipfile.ZipFile(src_path, 'r') as zip_ref:
                for info in zip_ref.infolist():
                    # 关键点：处理文件名编码
                    # ZipFile 默认使用 cp437，中文文件名通常需要转为 gbk
                    try:
                        filename = info.filename.encode('cp437').decode('gbk')
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
                        target_path = self.workspace_dir / f"{prefix}{src_path.stem}_{bare_name}"

                    # 执行解压并重命名
                    with zip_ref.open(info) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    
                    # 递归处理提取出来的 ZIP (如果有的话)
                    if target_path.suffix.lower() == '.zip':
                        self.unzip_recursive(target_path)
                        
        except Exception as e:
            print(f"解压 {src_path.name} 失败: {e}")

    def _is_zip(self, file_path: Path) -> bool:
        """判断是否为 ZIP 格式"""
        return file_path.suffix.lower() == '.zip'

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
        md_fence = _re.match(r'^```(?:json)?\s*\n?(.*?)\n?```\s*$', text, _re.DOTALL)
        if md_fence:
            text = md_fence.group(1).strip()

        # 2. 如果已经是合法 JSON，直接返回
        try:
            _json.loads(text)
            return text
        except Exception:
            pass

        # 3. 正则提取第一个完整的 JSON 对象 {...} 或数组 [...]
        for pattern in (r'(\{.*\})', r'(\[.*\])'):
            m = _re.search(pattern, text, _re.DOTALL)
            if m:
                candidate = m.group(1)
                try:
                    _json.loads(candidate)
                    return candidate
                except Exception:
                    pass

        return ""

    async def _call_with_json_retry(self, prompt: str, context_label: str,
                                     max_json_retries: int = 2) -> str:
        """
        调用 LLM 并确保返回合法 JSON 字符串。
        若 JSON 解析失败，将错误内容发回 LLM 要求自修复，最多重试 max_json_retries 次。
        """
        from ..utils.ai_utils import retry_llm_call
        import json as _json

        response = await retry_llm_call(
            self.client.models.generate_content,
            model=self.model_id,
            contents=prompt,
            config={'response_mime_type': 'application/json'}
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
                print(f"[JSON 自修复] {context_label} 第 {attempt+1} 次尝试失败，请求 LLM 修复...")
                repair_prompt = (
                    "以下 JSON 输出不完整或格式有误，请原样修复并输出完整合法的 JSON，不要添加任何解释：\n\n"
                    + raw[:3000]
                )
                response = await retry_llm_call(
                    self.client.models.generate_content,
                    model=self.model_id,
                    contents=repair_prompt,
                    config={'response_mime_type': 'application/json'}
                )
                raw = response.text
            else:
                print(f"[JSON 解析失败] {context_label} 达到最大重试次数，原始输出前 300 字：\n{raw[:300]}")

        return ""

    async def phase1_classify(self, text: str, prompt_template: str, teacher_files: List[str], reference_files: List[str]) -> dict:
        """阶段一：识别所有实验模块，分类并提取各模块对应的原文段落"""
        if not self.client:
            return {"experiments": []}

        prompt = prompt_template.replace("{{teacher_files}}", "\n".join(teacher_files) if teacher_files else "无")
        prompt = prompt.replace("{{reference_files}}", "\n".join(reference_files) if reference_files else "无")
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

    async def phase2_detail_verify(self, experiment: dict, prompt_template: str) -> List[dict]:
        """阶段二：仅针对验证性实验，将其 section_text 拆解为原子测试用例列表"""
        if not self.client:
            return []

        prompt = prompt_template.replace("{{experiment_name}}", experiment.get("name", ""))
        prompt = prompt.replace("{{source_circ}}", experiment.get("matched_source_circ") or "无")
        prompt = prompt.replace("{{target_subcircuit}}", experiment.get("target_subcircuit") or "main")
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

    # 保留旧方法兼容性（单元测试用）
    async def parse_tasks_with_llm(self, text: str, prompt_template: str, teacher_files: List[str], reference_files: List[str]) -> dict:
        return await self.phase1_classify(text, prompt_template, teacher_files, reference_files)


class ContentParsingAgent:
    """内容解析智能体总控"""
    
    def __init__(self, config, workspace_dir: Path, client):
        self.decompressor = DataDecompressor(workspace_dir)
        self.extractor = RequirementExtractor(client, config.gemini.model_flash)
        self.workspace_dir = workspace_dir

    def _categorize_workspace_files(self) -> Dict[str, List[Path]]:
        """
        对工作区内已平铺（且带 TEA_/REF_ 前缀）的文件进行分类。
        """
        files = list(self.workspace_dir.iterdir())
        categories = {
            "instruction_pdf": [],
            "report_template": [],
            "teacher_circuits": [],
            "reference_circuits": [],
            "reference_reports": [],
            "other": []
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
                if is_tea and ("报告" in pure_name or "模板" in pure_name or "样本" in pure_name):
                    categories["report_template"].append(f)
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
        
        # 2. 扫描并解压/拷贝所有文件到工作区
        ref_pattern = re.compile(r"^\d+[\+\s\-_]+[\u4e00-\u9fa5]+")
        for item in input_files:
            suffix = item.suffix.lower()
            if suffix == '.zip':
                self.decompressor.unzip_recursive(item)
            elif suffix in ('.circ', '.pdf', '.docx'):
                is_ref = ref_pattern.search(item.name) is not None
                prefix = "REF_" if is_ref else "TEA_"
                shutil.copy(item, self.workspace_dir / f"{prefix}{item.name}")
        
        # 3. 分类工作区文件
        cat = self._categorize_workspace_files()
        
        # 兜底读取解压后的指导书
        if not raw_text:
            for f in cat["instruction_pdf"]:
                raw_text += self.extractor.extract_text_from_pdf(f)
        
        # 4. 准备文件清单
        tea_names = [f.name for f in cat["teacher_circuits"]]
        ref_names = [f.name for f in cat["reference_circuits"]]

        # =========================================================
        # 阶段一：高层次分类——识别所有实验模块、类型、对应原文、电路关联
        # =========================================================
        p1_path = Path("prompts/parsing/phase1_classify.txt")
        p1_template = p1_path.read_text(encoding="utf-8") if p1_path.exists() else ""
        print("📋 [阶段一] 正在分类实验模块...")
        phase1_data = await self.extractor.phase1_classify(raw_text, p1_template, tea_names, ref_names)
        experiments = phase1_data.get("experiments", [])
        print(f"   → 识别出 {len(experiments)} 个实验模块")

        # =========================================================
        # 阶段二：仅对验证性实验进行原子级拆解
        # =========================================================
        p2_path = Path("prompts/parsing/phase2_verify_detail.txt")
        p2_template = p2_path.read_text(encoding="utf-8") if p2_path.exists() else ""

        all_tasks: List[TaskRecord] = []

        for exp in experiments:
            exp_name = exp.get("name", "未命名")
            exp_type = exp.get("task_type", "verification")
            exp_section = exp.get("section_text", "")     # Phase 1 提取的原始段落
            exp_desc = exp.get("description", "")
            s_name = exp.get("matched_source_circ")
            r_name = exp.get("matched_reference_circ")
            source_path = [str(self.workspace_dir / s_name)] if s_name and s_name != "null" else []
            ref_path = str(self.workspace_dir / r_name) if r_name and r_name != "null" else None

            if exp_type == "verification":
                # 阶段二：拆解为原子测试用例，同时把 section_text 喂入
                print(f"   🔬 [阶段二] 细化验证实验: {exp_name}")
                sub_items = await self.extractor.phase2_detail_verify(exp, p2_template)
                print(f"      → 拆解出 {len(sub_items)} 条测试用例")
                for sub in sub_items:
                    task = TaskRecord(
                        task_name=sub.get("task_name", exp_name),
                        task_type="verification",
                        analysis_raw=sub.get("description", ""),
                        section_text=exp_section,       # 保留原始段落供下游引用
                        target_subcircuit=exp.get("target_subcircuit"),
                        experiment_objective=exp_desc,
                        experiment_environment="",
                        thinking_questions=[]
                    )
                    task.source_circ = source_path
                    all_tasks.append(task)

            else:
                # 设计性/挑战性实验：保持单条，section_text 存入完整描述
                task = TaskRecord(
                    task_name=exp_name,
                    task_type=exp_type,
                    analysis_raw=exp_desc,
                    section_text=exp_section,           # 保留原始段落供下游引用
                    target_subcircuit=exp.get("target_subcircuit"),
                    experiment_objective=exp.get("description", ""),
                    experiment_environment="",
                    thinking_questions=[]
                )
                task.source_circ = source_path
                task.reference_circ = ref_path
                all_tasks.append(task)

        result = ParsingResult()
        result.verification_tasks = [t for t in all_tasks if t.task_type == "verification"]
        result.design_tasks = [t for t in all_tasks if t.task_type in ("design", "challenge")]
        result.reference_reports = [str(f) for f in cat["reference_reports"]]
        result.instruction_docs = (
            [str(f) for f in cat["instruction_pdf"]] +
            [str(f) for f in cat["report_template"]]
        )
        result.raw_experiments = experiments   # 阶段一全量数据（含 section_text），供下游全量参考
        
        return result
