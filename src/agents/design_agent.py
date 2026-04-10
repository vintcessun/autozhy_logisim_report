import asyncio
from pathlib import Path
from ..utils.xml_utils import CircuitLinter
from ..core.models import TaskRecord
import google.generativeai as genai

class DesignAgent:
    """
    设计性实验智能体。
    职责：根据需求生成/修改 .circ 文件。
    """

    def __init__(self, model, max_retries: int = 5):
        self.model = model
        self.max_retries = max_retries

    async def run(self, task: TaskRecord, template_path: Path) -> TaskRecord:
        """运行 Actor-Critic 循环"""
        if not template_path.exists():
            task.status = "failed"
            task.analysis_raw = f"找不到模板电路: {template_path}"
            return task

        current_xml = template_path.read_text(encoding="utf-8")
        attempt = 0
        
        while attempt < self.max_retries:
            attempt += 1
            # 1. Actor: 模型根据需求生成补丁
            generated_xml = await self._generate_xml_patch(task.task_name, task.analysis_raw, current_xml)
            
            # 2. 保存到临时文件进行校验
            temp_path = template_path.with_name(f"temp_{task.task_id}.circ")
            temp_path.write_text(generated_xml, encoding="utf-8")
            
            # 3. Critic: 静态拓扑校验
            linter = CircuitLinter(temp_path)
            is_valid, errors = linter.validate_topology()
            
            if is_valid:
                # 校验通过，正式保存并返回
                final_path = template_path.with_name(f"{task.task_name}_design.circ")
                temp_path.rename(final_path)
                task.source_circ = [str(final_path)]
                task.status = "finished"
                task.logic_check_pass = True
                return task
            else:
                # 校验失败，反馈给模型
                error_msg = "\n".join(errors)
                task.analysis_raw = f"第 {attempt} 次设计校验失败，错误：\n{error_msg}"
                current_xml = generated_xml  # 基于失败的结果继续修改
                
        task.status = "failed"
        return task

    async def _generate_xml_patch(self, task_name: str, feedback: str, xml: str) -> str:
        """调用 LLM 生成 XML 补丁"""
        # 读取提示词模板
        prompt_tmpl = Path("prompts/design/design_patch.txt").read_text(encoding="utf-8")
        prompt = prompt_tmpl.replace("{{design_goal}}", f"{task_name}\n反馈记录：{feedback}")
        prompt = prompt.replace("{{circ_xml}}", xml)
        
        # 实际调用代码将结合 genai.configure
        # 这里演示逻辑
        response = await self.model.generate_content_async(prompt)
        
        content = response.text.strip()
        # 处理可能的 markdown 代码块
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("xml"):
                content = content[3:]
        return content.strip()
