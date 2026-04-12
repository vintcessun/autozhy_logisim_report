import pytest
import sys
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path

# 全局注入 vendor 路径，确保测试环境能引用 logisim_logic
vendor_path = str(Path(__file__).parents[1] / "src" / "vendor")
if vendor_path not in sys.path:
    sys.path.append(vendor_path)

@pytest.fixture
def mock_genai_client():
    """Mock 现代化的 genai.Client 对象"""
    client = MagicMock()
    # 模拟 client.models.generate_content 链路
    client.models.generate_content = MagicMock()
    return client

@pytest.fixture
def sample_circ_xml():
    """基础测试用电路 XML"""
    return """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<logisim_project version="3.8.0">
  <circuit name="main">
    <comp lib="0" loc="(100,100)" name="Pin">
      <a name="label" val="In"/>
    </comp>
    <wire from="(100,100)" to="(200,100)"/>
    <comp lib="0" loc="(200,100)" name="Pin">
      <a name="label" val="Out"/>
      <a name="output" val="true"/>
    </comp>
  </circuit>
</logisim_project>"""
