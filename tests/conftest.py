import pytest
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path

@pytest.fixture
def mock_gemini_model():
    """Mock Gemini 模型对象"""
    model = MagicMock()
    model.generate_content_async = AsyncMock()
    return model

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
