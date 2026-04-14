import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# 确保 src 在导入路径中
sys.path.append(os.getcwd())
from src.agents.content_parsing import DataDecompressor, RequirementExtractor, ContentParsingAgent
from src.core.models import TaskRecord, ParsingResult

def test_data_decompressor_zip(tmp_path):
    """验证是否调用了 ZipFile 进行解压"""
    with patch("zipfile.is_zipfile", return_value=True):
        with patch("zipfile.ZipFile") as mock_zip:
            decompressor = DataDecompressor(tmp_path / "workspace")
            decompressor.unzip_recursive(Path("dummy.zip"))
            mock_zip.assert_called_once()

def test_extract_text_from_pdf():
    """测试 PDF 提取逻辑 (Mock pdfplumber)"""
    mock_pdf = MagicMock()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Hello Logisim"
    mock_pdf.pages = [mock_page]
    
    with patch("pdfplumber.open", return_value=MagicMock(__enter__=MagicMock(return_value=mock_pdf))):
        extractor = RequirementExtractor(None, "dummy-model")
        text = extractor.extract_text_from_pdf(Path("dummy.pdf"))
        assert "Hello Logisim" in text

def test_categorization_logic():
    """测试文件分类逻辑是否准确"""
    agent = ContentParsingAgent(MagicMock(), Path("workspace"), None)
    # 模拟工作区文件
    files = [
        Path("workspace/TEA_2026计算机组成原理实验-2(2).pdf"),
        Path("workspace/TEA_厦门大学计算机组成原理实验报告样本（第2次实验）(2).docx"),
        Path("workspace/TEA_starter.circ"),
        Path("workspace/REF_25120222201292+宋泽涛+第二次实验.zip"),
        Path("workspace/REF_Adder_Reference.circ")
    ]
    
    # 预先创建这些文件以供 _categorize_workspace_files 读取
    with patch("pathlib.Path.iterdir", return_value=files):
        cat = agent._categorize_workspace_files()
        
        # 验证分类结果
        assert any("实验" in f.name for f in cat["instruction_pdf"])
        assert any("样本" in f.name for f in cat["report_template"])
        assert any(".circ" in f.name for f in cat["teacher_circuits"])
        assert any("REF_" in f.name and ".circ" in f.name for f in cat["reference_circuits"])

@pytest.mark.asyncio
async def test_parse_tasks_with_llm():
    """测试利用 LLM 解析 JSON 任务"""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"tasks": [{"task_name": "Adder", "task_type": "design"}]}'
    
    with patch("src.utils.ai_utils.retry_llm_call", return_value=mock_response):
        extractor = RequirementExtractor(mock_client, "gemini-flash")
        tasks = await extractor.parse_tasks_with_llm("raw text", "template", [], [])
        assert tasks["tasks"][0]["task_name"] == "Adder"
        assert tasks["tasks"][0]["task_type"] == "design"

@pytest.mark.asyncio
async def test_parsing_agent_integrated():
    """全功能集成测试：调用真实模型解析 data_in 目录并将结果可视化输出"""
    import sys
    from src.utils.config_loader import ConfigManager
    from google import genai
    
    # 强制设置输出编码为 utf-8 以防 Windows 乱码
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    
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
    
    workspace_dir = Path("workspace")
    input_dir = Path("data_in")
    
    agent = ContentParsingAgent(app_config, workspace_dir, client)
    
    print("\n" + "="*70)
    print("🚀 [集成测试] 启动真实数据解析...")
    try:
        result = await agent.run(input_dir)
        
        total = len(result.verification_tasks) + len(result.design_tasks)
        print(f"\n✅ 解析成功！验证性实验 {len(result.verification_tasks)} 项，设计性实验 {len(result.design_tasks)} 项，共 {total} 个任务。\n")
        
        print(f"[1. 验证性实验] — 共 {len(result.verification_tasks)} 项")
        print("-"*70)
        for i, t in enumerate(result.verification_tasks, 1):
            print(f"  [{i:02d}] {t.task_name}")
            print(f"        起步电路: {Path(t.source_circ[0]).name if t.source_circ else '无'}")
            print(f"        描述: {t.analysis_raw[:120]}{'...' if len(t.analysis_raw) > 120 else ''}")
            
        print(f"\n[2. 设计性实验] — 共 {len(result.design_tasks)} 项")
        print("-"*70)
        for i, t in enumerate(result.design_tasks, 1):
            print(f"  [{i:02d}] {t.task_name}")
            print(f"        起步电路: {Path(t.source_circ[0]).name if t.source_circ else '无'}")
            print(f"        参考电路: {Path(t.reference_circ).name if t.reference_circ else '无'}")
            print(f"        描述: {t.analysis_raw[:120]}{'...' if len(t.analysis_raw) > 120 else ''}")
            
        print(f"\n[3. 报告参考资料] — 共 {len(result.reference_reports)} 项")
        print("-"*70)
        for r in result.reference_reports:
            print(f"  - {Path(r).name}")
            
        assert isinstance(result, ParsingResult)
        assert total > 0, "解析结果为空，请检查 Prompt 或输入文件"
    except Exception as e:
        print(f"❌ 解析失败: {e}")
        raise e
    finally:
        print("="*70 + "\n")
