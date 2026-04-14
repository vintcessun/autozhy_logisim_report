import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# 确保项目根目录在导入路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.report_agent import ReportAgent
from src.core.models import TaskRecord

@pytest.mark.asyncio
async def test_report_structure_mock(tmp_path):
    """Mock Pro + Flash，验证 Markdown 结构和资源拷贝"""
    mock_client = MagicMock()
    
    # 模拟 Pro 响应 (Intro)
    mock_intro_resp = MagicMock()
    mock_intro_resp.text = '{"experiment_environment": "Win10", "experiment_objective": "Test Obj", "abstract": "Test Abstract"}'
    
    # 模拟 Pro 响应 (Split)
    mock_split_resp = MagicMock()
    mock_split_resp.text = '{"section_32_ids": ["task1"], "section_33_ids": []}'
    
    # 模拟 Flash 响应 (Analysis)
    mock_flash_resp = MagicMock()
    mock_flash_resp.text = "This is a generated analysis paragraph."

    agent = ReportAgent(mock_client, "gemini-pro", "gemini-flash")
    
    # 模拟 LLM 调用
    async def mock_retry_call(fn, *args, **kwargs):
        content = str(kwargs.get("contents", ""))
        if "指导书" in content or "实验目的" in content or "intro" in content:
            return mock_intro_resp
        if "挑战性" in content or "challenge" in content:
            return mock_split_resp
        return mock_flash_resp

    tasks = [
        TaskRecord(task_id="task1", task_name="Adder", task_type="verification", analysis_raw="Raw 1", assets=["a.png"]),
        TaskRecord(task_id="task2", task_name="Mux", task_type="design", analysis_raw="Raw 2", assets=["b.png"], source_circ=["c.circ"])
    ]
    
    output_md = tmp_path / "report.md"
    
    # 创建模拟资源文件
    (tmp_path / "a.png").write_text("img1")
    (tmp_path / "b.png").write_text("img2")
    
    # 修改 assets 路径为临时文件路径
    tasks[0].assets = [str(tmp_path / "a.png")]
    tasks[1].assets = [str(tmp_path / "b.png")]
    
    with patch("src.agents.report_agent.retry_llm_call", side_effect=mock_retry_call):
        # 即使 read_text 返回空文件内容，我们的 mock 也能匹配到某些内容
        # 但为了稳妥，我们 mock Path.read_text 返回包含关键字的内容
        def mock_read_text(self, *args, **kwargs):
            if "intro.txt" in str(self): return "实验目的 指导书 {reference_content}"
            if "challenge_split.txt" in str(self): return "挑战性 {task_list}"
            return "{task_name} {section_text} {analysis_raw}"
            
        with patch("pathlib.Path.read_text", mock_read_text):
            await agent.generate(
                verification_tasks=[tasks[0]],
                design_tasks=[tasks[1]],
                design_sub_tasks=[],
                instruction_docs=[],
                reference_reports=[],
                output_path=output_md
            )
    
    assert output_md.exists()
    content = output_md.read_text(encoding="utf-8")
    assert "# Test Abstract" in content
    assert "## 1. 实验环境" in content
    assert "## 3.1 验证性实验" in content
    assert "![Adder](./实验报告.assets/a.png)" in content
    assert (tmp_path / "实验报告.assets" / "a.png").exists()

@pytest.mark.asyncio
async def test_report_agent_integrated():
    """真实 API 集成测试"""
    from src.utils.config_loader import ConfigManager
    from google import genai

    config_path = Path("config/config.toml")
    if not config_path.exists():
        pytest.skip("跳过集成测试：未找到 config/config.toml")

    app_config = ConfigManager.load_config(config_path)
    endpoint = app_config.gemini.base_url.rstrip('/')
    if endpoint.endswith('/v1beta'): endpoint = endpoint[:-7]
    elif endpoint.endswith('/v1'): endpoint = endpoint[:-3]
    
    client = genai.Client(
        api_key=app_config.gemini.api_key,
        http_options={'base_url': endpoint}
    )

    agent = ReportAgent(client, app_config.gemini.model_pro, app_config.gemini.model_flash)
    
    v_tasks = [TaskRecord(task_name="验证1", task_type="verification", analysis_raw="分析1")]
    d_tasks = [TaskRecord(task_id="d1", task_name="设计1", task_type="design", analysis_raw="分析2")]
    
    output_md = Path("output") / "integration_test_report.md"
    output_md.parent.mkdir(parents=True, exist_ok=True)
    
    await agent.generate(
        verification_tasks=v_tasks,
        design_tasks=d_tasks,
        design_sub_tasks=[],
        instruction_docs=[],
        reference_reports=[],
        output_path=output_md
    )
    
    assert output_md.exists()
    # 允许 API 失败时生成的默认文档
    content = output_md.read_text(encoding="utf-8")
    assert "## 1. 实验环境" in content
