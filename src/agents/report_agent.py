import asyncio
import logging
import re
import json
import copy
import shutil
from pathlib import Path
from typing import List, Dict
import pdfplumber
from docx import Document

# 静默 pdfminer 的字体元数据警告（FontBBox 缺失），不影响文字提取
logging.getLogger("pdfminer").setLevel(logging.ERROR)

from ..core.models import TaskRecord
from ..utils.ai_utils import generate_content_with_tools


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
        """主入口：汇总所有信息生成 Markdown 实验报告。"""
        print("\n[ReportAgent] 开始生成实验报告...")

        # 1. 提取所有输入文档的文本内容
        ref_content = self._extract_docs_text(instruction_docs + reference_reports)

        # 2. Phase A (Pro): 实验环境、目的、摘要
        intro_data = await self._generate_intro(ref_content)
        if isinstance(intro_data, list):
            intro_data = intro_data[0] if intro_data else {}

        # 3. Phase A (Pro): 挑战性任务识别
        task_split = await self._split_challenge_tasks(design_tasks)
        section_32_ids = task_split.get("section_32_ids", [])
        section_33_ids = task_split.get("section_33_ids", [])

        # 4. 拷贝资源文件到 output/ 目录
        assets_dir = output_path.parent / "实验报告.assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        self._copy_assets(
            verification_tasks + design_tasks + design_sub_tasks, assets_dir
        )

        # 5. 组装 Markdown 内容
        abstract_text = intro_data.get("abstract") or "计算机组成原理实验报告"
        report_md = f"# {abstract_text}\n\n"
        report_md += f"## 1. 实验环境\n\n{intro_data.get('experiment_environment', 'Windows系统下运行Logisim软件（需安装JDK）。')}\n\n"
        report_md += f"## 2. 实验目的\n\n{intro_data.get('experiment_objective', '验证与设计计算机组成原理相关电路。')}\n\n"

        # 3.1 验证性实验
        report_md += "## 3.1 验证性实验\n\n"
        for i, task in enumerate(verification_tasks, 1):
            analysis = await self._generate_task_analysis(task)
            answered_problems = await self._generate_problem_answers(task)
            task.problem_answers = answered_problems
            report_md += f"### ({i}) {task.task_name}\n\n"
            report_md += "#### 实验结果\n\n"
            for asset in task.assets:
                asset_name = Path(asset).name
                report_md += f"![{task.task_name}](./实验报告.assets/{asset_name})\n\n"
            report_md += f"{analysis}\n\n"
            report_md += "#### 实验分析\n\n"
            report_md += f"{task.analysis_raw}\n\n"
            if answered_problems:
                report_md += "#### 回答问题\n\n"
                for idx, item in enumerate(answered_problems, 1):
                    report_md += f"{idx}. {item.get('problem', '')}\n\n{item.get('answer', '')}\n\n"

        # 3.2 设计实验
        report_md += "## 3.2 设计实验\n\n"
        d_idx = 1
        for task in design_tasks:
            if task.task_id in section_32_ids:
                report_md += await self._build_design_section(
                    task, design_sub_tasks, d_idx
                )
                d_idx += 1

        # 3.3 挑战性实验
        if section_33_ids:
            report_md += "## 3.3 挑战性实验\n\n"
            c_idx = 1
            for task in design_tasks:
                if task.task_id in section_33_ids:
                    report_md += await self._build_design_section(
                        task, design_sub_tasks, c_idx
                    )
                    c_idx += 1

        # 6. 保存报告
        output_path.write_text(report_md, encoding="utf-8")
        print(f"[ReportAgent] 实验报告已生成: {output_path}")
        return output_path

    async def _build_design_section(
        self, task: TaskRecord, sub_tasks: List[TaskRecord], idx: int
    ) -> str:
        """构建单个设计/挑战性实验小节"""
        section = f"### ({idx}) {task.task_name}\n\n"
        section += "#### 电路设计\n\n"
        # 参考图通常排在 task.assets 的第一位（DesignAgent.run 中插入的）
        if task.assets:
            ref_img = Path(task.assets[0]).name
            section += f"![参考电路设计](./实验报告.assets/{ref_img})\n\n"

        section += "#### 实验结果\n\n"
        # 找到属于该任务的子验证任务
        relevant_subs = [s for s in sub_tasks if s.source_circ == task.source_circ]
        for sub in relevant_subs:
            sub_analysis = await self._generate_task_analysis(sub)
            section += f"##### {sub.task_name}\n\n"
            for asset in sub.assets:
                asset_name = Path(asset).name
                section += f"![验证结果](./实验报告.assets/{asset_name})\n\n"
            section += f"{sub_analysis}\n\n"

        section += "#### 实验分析\n\n"
        # 读取 DesignAgent 汇总后的验证性实验分析
        section += f"{task.analysis_raw}\n\n"
        answered_problems = await self._generate_problem_answers(task)
        task.problem_answers = answered_problems
        if answered_problems:
            section += "#### 回答问题\n\n"
            for idx, item in enumerate(answered_problems, 1):
                section += (
                    f"{idx}. {item.get('problem', '')}\n\n{item.get('answer', '')}\n\n"
                )
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

    def _copy_assets(self, tasks: List[TaskRecord], assets_dir: Path):
        """将所有截图拷贝到 output/实验报告.assets/"""
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
                        shutil.copy2(src, dst)
                    except shutil.SameFileError:
                        pass
                    except Exception as e:
                        print(f"[ReportAgent] 拷贝截图 {src.name} 失败: {e}")
                else:
                    print(f"[ReportAgent] 警告: 找不到截图 {asset_path}")
