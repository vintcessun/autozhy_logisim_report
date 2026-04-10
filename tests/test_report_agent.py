import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from src.agents.report_agent import ReportAgent
from src.core.models import TaskRecord

@pytest.mark.asyncio
async def test_report_orchestration(mock_gemini_model, tmp_path):
    """测试报告拼装逻辑"""
    mock_response = MagicMock()
    mock_response.text = "Refined analysis text."
    mock_gemini_model.generate_content_async.return_value = mock_response
    
    agent = ReportAgent(mock_gemini_model)
    tasks = [
        TaskRecord(
            task_name="AdderTest",
            task_type="verification",
            analysis_raw="Original draft",
            assets=["adder.png"]
        )
    ]
    
    output_path = tmp_path / "report.md"
    # 我们还需要模拟提示词模板的读取
    with patch("pathlib.Path.read_text", return_value="Template {{task_name}} {{raw_analysis}}"):
        await agent.orchestrate(tasks, output_path)
    
    content = output_path.read_text(encoding="utf-8")
    assert "# 计算机组成原理实验报告" in content
    assert "### AdderTest" in content
    assert "Refined analysis text." in content
    assert "![AdderTest 结果图](./adder.png)" in content
