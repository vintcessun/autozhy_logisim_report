import pytest
from src.agents.design_agent import DesignAgent
from src.agents.verification_agent import VerificationAgent
from src.core.models import TaskRecord
from pathlib import Path
from unittest.mock import MagicMock

@pytest.mark.asyncio
async def test_design_agent_loop(mock_gemini_model, tmp_path):
    """测试设计智能体的修正循环"""
    valid_xml = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<logisim_project version="3.8.0">
  <circuit name="main">
    <comp lib="0" loc="(100,100)" name="Pin"/>
    <wire from="(100,100)" to="(200,100)"/>
    <comp lib="0" loc="(200,100)" name="Pin"/>
  </circuit>
</logisim_project>"""
    
    # 模拟第一次输出：Wire 到达 (200,100)，但组件被挪到了 (500,500)，这将导致 Lint 失败
    broken_xml = valid_xml.replace('loc="(200,100)"', 'loc="(500,500)"')
    
    # 设置 Mock 返回值序列
    mock_response_1 = MagicMock()
    mock_response_1.text = f"```xml\n{broken_xml}\n```"
    
    mock_response_2 = MagicMock()
    mock_response_2.text = f"```xml\n{valid_xml}\n```"
    
    mock_gemini_model.generate_content_async.side_effect = [mock_response_1, mock_response_2]
    
    agent = DesignAgent(mock_gemini_model, max_retries=3)
    task = TaskRecord(task_name="TestTask", task_type="design")
    
    template_path = tmp_path / "template.circ"
    template_path.write_text(valid_xml, encoding="utf-8")
    
    result = await agent.run(task, template_path)
    
    assert result.status == "finished"
    assert result.logic_check_pass is True
    # 验证是否重试了 (调用了两次模型)
    assert mock_gemini_model.generate_content_async.call_count == 2
