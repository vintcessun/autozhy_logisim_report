from src.utils.xml_utils import CircuitLinter
from pathlib import Path

def test_linter_with_valid_xml(tmp_path, sample_circ_xml):
    """测试合法 XML 是否通过校验"""
    c_path = tmp_path / "valid.circ"
    c_path.write_text(sample_circ_xml, encoding="utf-8")
    
    linter = CircuitLinter(c_path)
    is_valid, errors = linter.validate_topology()
    assert is_valid is True
    assert len(errors) == 0

def test_linter_with_dangling_wire(tmp_path):
    """测试带悬空连线的 XML 是否失败"""
    broken_xml = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<logisim_project version="3.8.0">
  <circuit name="main">
    <comp lib="0" loc="(100,100)" name="Pin"/>
    <wire from="(100,100)" to="(500,500)"/> <!-- 500,500 处无组件 -->
  </circuit>
</logisim_project>"""
    c_path = tmp_path / "broken.circ"
    c_path.write_text(broken_xml, encoding="utf-8")
    
    linter = CircuitLinter(c_path)
    is_valid, errors = linter.validate_topology()
    assert is_valid is False
    assert any("500, 500" in err for err in errors)
