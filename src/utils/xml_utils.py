import sys
from pathlib import Path
from typing import Tuple, List
import logisim_logic

class CircuitLinter:
    """利用 logisim_logic 引擎进行高级电路校验"""

    def __init__(self, circ_path: Path):
        self.circ_path = circ_path

    def validate_topology(self) -> Tuple[bool, List[str]]:
        """
        验证电路的逻辑一致性。
        利用库的逻辑提取能力，如果电路存在致命结构错误（如非法连接），库会抛出异常。
        """
        errors = []
        try:
            proj = logisim_logic.load_project(self.circ_path)
            for circuit in proj.circuits:
                # 尝试提取逻辑网络，这会自动检查连线有效性
                logisim_logic.extract_logical_circuit(circuit, project=proj)
            return True, []
        except Exception as e:
            errors.append(f"逻辑校验失败: {str(e)}")
            return False, errors

    def fix_xml_formatting(self):
        """利用库的 save_project 自带的格式化功能"""
        try:
            proj = logisim_logic.load_project(self.circ_path)
            logisim_logic.save_project(proj, self.circ_path)
        except Exception:
            pass

class CircuitAnalyzer:
    """电路结构语义分析器"""
    
    def __init__(self, circ_path: Path):
        self.circ_path = circ_path

    def get_structure_summary(self) -> str:
        """获取高度语义化的电路连接摘要"""
        try:
            proj = logisim_logic.load_project(self.circ_path)
            summary = []
            for circuit in proj.circuits:
                logical = logisim_logic.extract_logical_circuit(circuit, project=proj)
                summary.append(f"电路 '{circuit.name}' 逻辑快照:")
                # 利用库的 __str__ 方法输出格式化的网络结构
                summary.append(str(logical))
            return "\n".join(summary)
        except Exception as e:
            return f"电路分析失败: {e}"
