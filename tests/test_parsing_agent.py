import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from src.agents.content_parsing import DataDecompressor, RequirementExtractor

def test_data_decompressor_path_injection():
    """验证是否正确将 3rd 路径注入环境变量"""
    with patch("os.environ", {"PATH": "initial_path"}):
        with patch("pathlib.Path.mkdir"):
            # 假设 3rd/7z.exe 存在
            decompressor = DataDecompressor(Path("workspace"), bin_7z_path="3rd/7z.exe")
            assert "3rd" in os.environ["PATH"]

def test_extract_text_from_pdf(tmp_path):
    """测试 PDF 提取逻辑 (Mock pdfplumber)"""
    mock_pdf = MagicMock()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Hello Logisim"
    mock_pdf.pages = [mock_page]
    
    with patch("pdfplumber.open", return_value=MagicMock(__enter__=MagicMock(return_value=mock_pdf))):
        extractor = RequirementExtractor(None)
        text = extractor.extract_text_from_pdf(Path("dummy.pdf"))
        assert "Hello Logisim" in text

@pytest.mark.asyncio
async def test_parse_tasks_with_llm(mock_gemini_model):
    """测试利用 LLM 解析 JSON 任务"""
    mock_response = MagicMock()
    mock_response.text = '{"tasks": [{"task_name": "Adder", "task_type": "verification"}]}'
    mock_gemini_model.generate_content_async.return_value = mock_response
    
    extractor = RequirementExtractor(mock_gemini_model)
    tasks = await extractor.parse_tasks_with_llm("raw text", "template")
    assert tasks["tasks"][0]["task_name"] == "Adder"
import os
