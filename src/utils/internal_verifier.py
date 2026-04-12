import sys
import random
from pathlib import Path
from typing import Dict, Any

# Ensure vendor and project path are in sys.path
project_root = Path(__file__).parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

vendor_path = project_root / "src" / "vendor"
if str(vendor_path) not in sys.path:
    sys.path.insert(0, str(vendor_path))

from logisim_logic import load_project, extract_logical_circuit
from src.utils.logic_simulator import LogicSimulator

def self_verify_cla(circ_path: str, num_random: int = 50) -> str:
    """
    Flash 模型专用的自检工具。
    基于纯数学真值表 (A+B+Cin) 校验电路逻辑。
    """
    results = []
    try:
        path_obj = Path(circ_path)
        if not path_obj.exists():
            return f"Error: 文件 {circ_path} 不存在。"

        proj = load_project(str(path_obj))
        # 优先使用主电路
        circ = proj.main_circuit if hasattr(proj, 'main_circuit') and proj.main_circuit else (proj.circuits[0] if proj.circuits else None)
        if not circ:
            return "Error: 项目中未找到有效电路。"

        logical = extract_logical_circuit(circ, project=proj)
        sim = LogicSimulator(logical)
        
        # 定义测试矩阵：固定边界 + 随机
        test_cases = [
            (0, 0, 0),
            (0xFFFF, 0, 0),
            (0, 0xFFFF, 0),
            (0xFFFF, 0xFFFF, 1),
            (0x7FFF, 0x0001, 0),
            (0x8000, 0x8000, 0),
        ]
        for _ in range(num_random):
            test_cases.append((random.randint(0, 0xFFFF), random.randint(0, 0xFFFF), random.randint(0, 1)))
            
        passed = 0
        total = len(test_cases)
        errors = []

        for a, b, cin in test_cases:
            inputs = {"A": a, "B": b, "Cin": cin}
            try:
                outputs = sim.simulate(inputs)
                s_val = outputs.get("S")
                cout_val = outputs.get("Cout")
                
                # 数学真理
                expected_sum = a + b + cin
                expected_s = expected_sum & 0xFFFF
                expected_cout = (expected_sum >> 16) & 1
                
                if s_val is None or cout_val is None:
                    errors.append(f"输入 A={a:04X}, B={b:04X}, Cin={cin} -> 输出引脚缺失 (got S={s_val}, Cout={cout_val})")
                    continue
                
                if s_val != expected_s or cout_val != expected_cout:
                    errors.append(f"差异检测！输入 A={a:04X}, B={b:04X}, Cin={cin} -> 预期 S={expected_s:04X}, Cout={expected_cout} | 实际 S={s_val:04X}, Cout={cout_val}")
                else:
                    passed += 1
            except Exception as e:
                errors.append(f"仿真异常 (A={a:X}, B={b:X}): {str(e)}")
        
        if passed == total:
            return f"SUCCESS: 内部数学校验 100% 通过 ({passed}/{total})！该设计已经符合加法逻辑。"
        else:
            err_log = "\n".join(errors[:10]) # 只返回前10个错误
            if len(errors) > 10:
                err_log += f"\n... 以及另外 {len(errors)-10} 个错误。"
            return f"FAILURE: 校验未通过 ({passed}/{total})\n详细报告：\n{err_log}"

    except Exception as e:
        return f"CRITICAL_ERROR: 工具执行异常: {str(e)}"

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(self_verify_cla(sys.argv[1]))
    else:
        print("Usage: python internal_verifier.py <path_to_circ>")
