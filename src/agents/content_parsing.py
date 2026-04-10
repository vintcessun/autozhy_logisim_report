import os
import shutil
from pathlib import Path
import patoolib
import pdfplumber
from docx import Document
from typing import List
import google.generativeai as genai
from ..core.models import TaskRecord

class DataDecompressor:
    """递归解压工具，利用 patool 支持多种格式"""
    
    def __init__(self, workspace_dir: Path, bin_7z_path: str = "3rd/7z.exe"):
        self.workspace_dir = workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        
        # 将 7z 所在目录加入 PATH，以便 patool 找到它
        bin_dir = str(Path(bin_7z_path).parent.absolute())
        if bin_dir not in os.environ["PATH"]:
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]

    def unzip_recursive(self, src_path: Path):
        """
        递归解压并将所有内容平铺到 workspace 目录。
        符合 ADR-0001: '平铺到工作区'。
        """
        temp_extract_dir = self.workspace_dir / "temp_extract"
        temp_extract_dir.mkdir(exist_ok=True)
        
        try:
            # 使用 patool 解压
            patoolib.extract_archive(str(src_path), outdir=str(temp_extract_dir), verbosity=-1)
            
            # 遍历提取后的内容
            for root, dirs, files in os.walk(temp_extract_dir):
                for file in files:
                    file_path = Path(root) / file
                    # 如果是压缩包，递归处理
                    if self._is_archive(file_path):
                        self.unzip_recursive(file_path)
                    else:
                        # 平铺：直接移动到 workspace 根目录（处理重名冲突）
                        target_path = self.workspace_dir / file
                        if target_path.exists():
                            target_path = self.workspace_dir / f"{src_path.stem}_{file}"
                        shutil.move(str(file_path), str(target_path))
        finally:
            # 清理临时目录
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)

    def _is_archive(self, file_path: Path) -> bool:
        """判断是否为压缩格式"""
        extensions = ('.zip', '.rar', '.7z', '.tar', '.gz')
        return file_path.suffix.lower() in extensions

class RequirementExtractor:
    """需求提取器，利用 pdfplumber 和 python-docx"""
    
    def __init__(self, gemini_model):
        self.model = gemini_model

    def extract_text_from_pdf(self, pdf_path: Path) -> str:
        """提取 PDF 文本"""
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text

    def extract_text_from_docx(self, docx_path: Path) -> str:
        """提取 DOCX 文本"""
        doc = Document(docx_path)
        return "\n".join([para.text for para in doc.paragraphs])

    async def parse_tasks_with_llm(self, text: str, prompt_template: str) -> List[dict]:
        """利用 Gemini 3 Flash 将文本转化为结构化任务清单"""
        if not self.model:
            return {}

        prompt = f"{prompt_template}\n\n待解析文本：\n{text}"
        
        # 使用 Gemini 3 Flash 进行生成
        response = await self.model.generate_content_async(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
            )
        )
        
        import json
        try:
            # 去除可能存在的 markdown 标记
            content = response.text.strip()
            if content.startswith("```json"):
                content = content[7:-3].strip()
            return json.loads(content)
        except Exception as e:
            print(f"解析 Gemini 返回的 JSON 失败: {e}")
            return {}

class CircAssociator:
    """电路关联器，将电路文件与任务进行匹配"""
    
    def match_tasks(self, tasks: List[TaskRecord], circ_files: List[Path]) -> List[TaskRecord]:
        """将电路文件与提取的任务进行匹配"""
        circ_map = {f.stem.lower(): f for f in circ_files}
        for task in tasks:
            # 基础匹配：按名称匹配
            task_name_lower = task.task_name.lower()
            for stem, path in circ_map.items():
                if stem in task_name_lower or task_name_lower in stem:
                    task.source_circ = [str(path)]
                    break
        return tasks

class ContentParsingAgent:
    """内容解析智能体总控"""
    
    def __init__(self, config, workspace_dir: Path):
        self.decompressor = DataDecompressor(workspace_dir)
        # 初始化 Gemini 模型 (后续通过 Config 传入)
        self.extractor = RequirementExtractor(None)
        self.associator = CircAssociator()

    async def run(self, input_dir: Path) -> List[TaskRecord]:
        """
        执行内容解析全流程：
        1. 递归解压所有包并平铺。
        2. 搜索指导书与报告样本。
        3. 提取文本并利用 LLM 结构化。
        4. 关联电路文件。
        """
        # 1. 解压与扫描输入
        if not input_dir.exists():
            return []
            
        for item in input_dir.iterdir():
            if self.decompressor._is_archive(item):
                self.decompressor.unzip_recursive(item)
            elif item.suffix == '.circ':
                shutil.copy(item, self.decompressor.workspace_dir / item.name)
        
        # 2. 在平铺后的工作区中寻找指导书 (.pdf, .docx)
        workspace_files = list(self.decompressor.workspace_dir.iterdir())
        instruction_files = [f for f in workspace_files if f.suffix.lower() in ('.pdf', '.docx')]
        circ_files = [f for f in workspace_files if f.suffix.lower() == '.circ']
        
        if not instruction_files:
            # 没找到指导书，可能是纯电路包
            return [TaskRecord(task_name=f.stem, task_type="verification", source_circ=[str(f)]) for f in circ_files]

        # 3. 提取所有指导书文本
        raw_text = ""
        for f in instruction_files:
            if f.suffix.lower() == '.pdf':
                raw_text += self.extractor.extract_text_from_pdf(f)
            else:
                raw_text += self.extractor.extract_text_from_docx(f)
        
        # 4. 调用 LLM 提取任务大纲
        prompt_path = Path("prompts/parsing/task_extraction.txt")
        template = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "提取实验任务内容"
        
        parsed_data = await self.extractor.parse_tasks_with_llm(raw_text, template)
        
        # 5. 组织 TaskRecord 列表
        objective = parsed_data.get("objective", "")
        environment = parsed_data.get("environment", "")
        thinking_qs = parsed_data.get("thinking_questions", [])
        
        tasks: List[TaskRecord] = []
        for task_item in parsed_data.get("tasks", []):
            task = TaskRecord(
                task_name=task_item.get("task_name", "未命名任务"),
                task_type=task_item.get("task_type", "verification"),
                analysis_raw=task_item.get("description", ""),
                experiment_objective=objective,
                experiment_environment=environment,
                thinking_questions=thinking_qs
            )
            tasks.append(task)
            
        # 6. 电路关联
        self.associator.match_tasks(tasks, circ_files)
            
        return tasks
