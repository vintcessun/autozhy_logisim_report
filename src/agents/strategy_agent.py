import asyncio
from pathlib import Path
from ..utils.ai_utils import generate_content_with_tools


class StrategyAgent:
    """
    Pro 模型：负责 16 位 CLA 的架构设计与分解。
    具有全量工具权限。
    """

    def __init__(self, client, model_id: str):
        self.client = client
        self.model_id = model_id

    async def generate_design_spec(self, info_path: Path, current_analysis: str) -> str:
        """
        调研并输出详细的设计规格。
        """
        info_text = (
            info_path.read_text(encoding="utf-8")
            if info_path.exists()
            else "无任务描述"
        )

        prompt = f"""你是一个高级数字电路设计师（Pro 级别）。
任务：设计一个 16 位先行进位加法器 (CLA)。
要求：组内并行、组间并行设计。

你的角色：StrategyAgent
你的职责：
1. 调研 16 位 CLA 的详细布线逻辑和进位公式。
2. 拆解任务，为执行模型 (Flash) 提供清晰的电路组件清单和连接逻辑。
3. 必须通过搜索工具 (search_web) 确认公式的准确性。
4. 可以使用 python_interpreter 验证公式在数学上是否成立。

当前设计状态：
{current_analysis}

任务需求 (info.txt)：
{info_text}

请输出一份详细的【电路规格说明书】，包含：
- 数据位宽处理方式
- 进位链的分层结构 (4x4 逻辑)
- 对应的逻辑表达式
- 各组件的命名规范建议
"""

        response = await generate_content_with_tools(
            self.client,
            model=self.model_id,
            contents=prompt,
        )

        return response.text.strip()
