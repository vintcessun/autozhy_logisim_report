import pytest
from src.agents.design_agent import DesignAgent
from src.agents.verification_agent import VerificationAgent
from src.core.models import TaskRecord
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_design_agent_loop(mock_genai_client, tmp_path):
    """测试设计智能体的修正循环"""
    valid_xml = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<logisim_project version="3.8.0">
  <circuit name="main">
    <comp lib="0" loc="(100,100)" name="Pin"/>
  </circuit>
</logisim_project>"""
    broken_xml = valid_xml.replace('loc="(100,100)"', 'loc="(500,500)"')
    
    # Mock 返回值: 现代 SDK 语法
    mock_response_1 = MagicMock(); mock_response_1.text = f"```xml\n{broken_xml}\n```"
    mock_response_2 = MagicMock(); mock_response_2.text = f"```xml\n{valid_xml}\n```"
    mock_genai_client.models.generate_content.side_effect = [mock_response_1, mock_response_2]
    
    agent = DesignAgent(mock_genai_client, "gemini-pro", max_retries=3)
    task = TaskRecord(task_name="TestDesign", task_type="design")
    
    template_path = tmp_path / "template.circ"
    template_path.write_text(valid_xml, encoding="utf-8")
    
    result = await agent.run(task, template_path)
    assert result.status == "finished"
    assert result.logic_check_pass is True

@pytest.mark.asyncio
async def test_content_parsing_agent_io(mock_genai_client, tmp_path):
    """测试内容解析智能体的 I/O 契约"""
    from src.agents.content_parsing import ContentParsingAgent
    
    # 模拟 LLM 返回的结构化 JSON
    mock_json = {
        "objective": "测试实验",
        "tasks": [{"task_name": "Task1", "task_type": "verification", "description": "Doing something"}]
    }
    mock_response = MagicMock(); mock_response.text = f'```json\n{str(mock_json).replace("\'", "\"")}\n```'
    mock_genai_client.models.generate_content.return_value = mock_response
    
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "data_in"
    input_dir.mkdir(); (input_dir / "test.pdf").write_text("dummy")
    
    # 注入现代版客户端
    # 注入配置 Mock 并明确返回结果
    config = MagicMock()
    config.gemini.model_flash = "gemini-flash"
    agent = ContentParsingAgent(config, workspace, mock_genai_client)
    
    tasks = await agent.run(input_dir)
    assert len(tasks) == 1
    assert tasks[0].task_name == "Task1"
    assert tasks[0].experiment_objective == "测试实验"

@pytest.mark.asyncio
async def test_verification_agent_io(tmp_path, mocker):
    """测试验证智能体的 I/O 契约 (Mock 模式)"""
    from src.agents.verification_agent import VerificationAgent
    
    # Mock 掉耗时的视觉和仿真组件
    mock_emu_cls = mocker.patch("src.agents.verification_agent.LogisimEmulator")
    # 确保初始化方法是可 await 的
    mock_emu_cls.return_value.launch_and_initialize = AsyncMock(return_value=True)
    mocker.patch("src.agents.verification_agent.TarsBridge.perform_visual_action", return_value=True)
    mocker.patch("pyautogui.screenshot")
    mocker.patch("src.agents.verification_agent.ScreenLockContext")
    mocker.patch("src.agents.verification_agent.screen_lock")

    config = MagicMock()
    config.ollama.endpoint = "http://localhost:11434"
    mock_client = MagicMock()
    # 注入现代版客户端
    agent = VerificationAgent(config, mock_client)
    
    task = TaskRecord(task_name="TestVerify", task_type="verification")
    circ_path = tmp_path / "test.circ"
    circ_path.write_text("xml", encoding="utf-8")
    
    result = await agent.run(task, circ_path)
    
    assert result.status == "finished"
    assert len(result.assets) > 0
    assert "verified" in result.assets[0]

@pytest.mark.asyncio
async def test_report_agent_io(mock_genai_client, tmp_path):
    """测试报告智能体的 I/O 契约"""
    from src.agents.report_agent import ReportAgent
    
    mock_response = MagicMock(); mock_response.text = "Refined analysis text."
    mock_genai_client.models.generate_content.return_value = mock_response
    
    # 注入现代版客户端
    agent = ReportAgent(mock_genai_client, "gemini-flash")
    tasks = [
        TaskRecord(task_name="TaskA", task_type="verification", assets=["img.png"], analysis_raw="Raw logic")
    ]
    output_path = tmp_path / "final_report.md"
    
    final_path = await agent.orchestrate(tasks, output_path)
    
    assert final_path.exists()
    content = final_path.read_text(encoding="utf-8")
    assert "TaskA" in content
    assert "Refined analysis text." in content
    assert "![TaskA 结果图]" in content
