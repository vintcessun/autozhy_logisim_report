import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Set, Tuple, List

class CircuitLinter:
    """静态电路校验器，执行规格书要求的 Actor-Critic 闭环校验"""

    def __init__(self, circ_path: Path):
        self.circ_path = circ_path
        self.tree: ET.ElementTree = ET.parse(circ_path)
        self.root = self.tree.getroot()

    def validate_topology(self) -> Tuple[bool, List[str]]:
        """
        验证逻辑拓扑可达性。
        规则：每一条 <wire> 的 from 和 to 必须连接在某个组件或另一条线的端点上。
        """
        errors = []
        valid_points: Set[Tuple[int, int]] = self._collect_connection_points()
        
        # 扫描所有 wire
        for circuit in self.root.findall(".//circuit"):
            for wire in circuit.findall("wire"):
                f = self._parse_coord(wire.get("from"))
                t = self._parse_coord(wire.get("to"))
                
                if f not in valid_points:
                    errors.append(f"悬空连线起点: {f} 在电路 {circuit.get('name')} 中")
                if t not in valid_points:
                    errors.append(f"悬空连线终点: {t} 在电路 {circuit.get('name')} 中")
                
                # 线本身也提供连接点
                valid_points.add(f)
                valid_points.add(t)

        return len(errors) == 0, errors

    def _collect_connection_points(self) -> Set[Tuple[int, int]]:
        """收集所有组件的物理连接点"""
        points = set()
        for circuit in self.root.findall(".//circuit"):
            # 记录所有组件的 loc
            for comp in circuit.findall("comp"):
                loc = self._parse_coord(comp.get("loc"))
                if loc:
                    points.add(loc)
        return points

    def _parse_coord(self, coord_str: str) -> Tuple[int, int]:
        """解析 '(x,y)' 格式的坐标"""
        if not coord_str:
            return None
        try:
            # 去掉括号并拆分
            cleaned = coord_str.strip("()")
            x, y = map(int, cleaned.split(","))
            return (x, y)
        except Exception:
            return None

    def fix_xml_formatting(self):
        """格式化并清理 XML"""
        ET.indent(self.tree, space="  ", level=0)
        self.tree.write(self.circ_path, encoding="utf-8", xml_declaration=True)
