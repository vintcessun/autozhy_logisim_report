import asyncio
from pathlib import Path
from typing import List
from ..core.models import TaskRecord
import google.generativeai as genai

class ReportAgent:
    """
    实验报告智能体 / 编排器 (Orchestrator)。
    职责：统筹全局流程，组合并润色最终报告。
    """

    def __init__(self, model):
        self.model = model

    async def orchestrate(self, tasks: List[TaskRecord], output_path: Path) -> Path:
        """全局调度并生成最终报告"""
        report_content = "# 计算机组成原理实验报告\n\n"
        
        # 补充通用信息 (实验环境、目的)
        if tasks:
            report_content += f"## 1. 实验环境\n{tasks[0].experiment_environment}\n\n"
            report_content += f"## 2. 实验目的\n{tasks[0].experiment_objective}\n\n"

        report_content += "## 3. 实验内容与验证\n\n"

        for task in tasks:
            # 1. 任务分析润色
            refined_text = await self._refine_analysis(task)
            
            # 2. 拼装 Markdown
            section = f"### {task.task_name}\n\n"
            section += f"{refined_text}\n\n"
            
            # 插入关联图片
            for asset in task.assets:
                section += f"![{task.task_name} 结果图](./{asset})\n\n"
            
            report_content += section

        # 3. 插入思考题
        report_content += "## 4. 思考题答案\n\n"
        # 收集去重后的问题并回答
        all_questions = set()
        for task in tasks:
            for q in task.thinking_questions:
                all_questions.add(q)
        
        for q in all_questions:
            answer = await self._answer_thinking_question(q, report_content)
            report_content += f"**Q: {q}**\n\n**A: {answer}**\n\n"

        # 4. 写入文件
        output_path.write_text(report_content, encoding="utf-8")
        return output_path

    async def _refine_analysis(self, task: TaskRecord) -> str:
        """调用 Gemini 3 Flash 进行文字润色"""
        prompt_tmpl = Path("prompts/report/analysis_refinement.txt").read_text(encoding="utf-8")
        
        # 处理图片路径
        img_path = task.assets[0] if task.assets else "暂无图片"
        
        prompt = prompt_tmpl.replace("{{task_name}}", task.task_name)
        prompt = prompt.replace("{{raw_analysis}}", task.analysis_raw)
        prompt = prompt.replace("{{image_path}}", img_path)
        
        response = await self.model.generate_content_async(prompt)
        return response.text.strip()
    async def _answer_thinking_question(self, question: str, context: str) -> str:
        """模型基于实验上下文回答思考题"""
        prompt = f"依据以下实验背景资料，回答思考题：\n\n【背景】\n{context[:2000]}\n\n【问题】\n{question}"
        response = await self.model.generate_content_async(prompt)
        return response.text.strip()
