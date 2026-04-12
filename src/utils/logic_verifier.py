import sys
import random
from pathlib import Path
from typing import Dict, List, Any, Optional

# Ensure vendor path
vendor_path = str(Path(__file__).parents[2] / "src" / "vendor")
if vendor_path not in sys.path:
    sys.path.insert(0, vendor_path)

from logisim_logic import load_project, extract_logical_circuit
from .logic_simulator import LogicSimulator

def verify_16bit_adder(circ_path: str, num_tests=100) -> Dict[str, Any]:
    """
    Verify a 16-bit CLA circuit using truth-table simulation.
    Returns: { "success": bool, "feedback": str }
    """
    try:
        proj = load_project(circ_path)
        main_name = proj.main.name if proj.main else None
        if main_name and any(c.name == main_name for c in proj.circuits):
            circuit = next(c for c in proj.circuits if c.name == main_name)
        else:
            circuit = proj.circuits[0]
            
        logical = extract_logical_circuit(circuit, project=proj)
        sim = LogicSimulator(logical)
    except Exception as e:
        return {"success": False, "feedback": f"电路解析/提取逻辑失败: {str(e)}"}
    
    # Test cases: Edge cases + Random
    test_cases = [
        (0, 0, 0),
        (0xFFFF, 0, 0),
        (0, 0xFFFF, 0),
        (0xFFFF, 1, 0),
        (0xFFFF, 0xFFFF, 1),
        (0x0001, 0x0001, 0),
        (0x000F, 0x0001, 0),
    ]
    for _ in range(num_tests):
        test_cases.append((random.randint(0, 0xFFFF), random.randint(0, 0xFFFF), random.randint(0, 1)))
        
    errors = []
    for a, b, cin in test_cases:
        inputs = {"A": a, "B": b, "Cin": cin}
        try:
            outputs = sim.simulate(inputs)
            
            total = a + b + cin
            expected_s = total & 0xFFFF
            expected_cout = (total >> 16) & 1
            
            got_s = outputs.get("S")
            got_cout = outputs.get("Cout")
            
            if got_s is None or got_cout is None:
                missing = []
                if got_s is None: missing.append("S")
                if got_cout is None: missing.append("Cout")
                errors.append(f"输入 A={a:04X}, B={b:04X}, Cin={cin} -> 输出缺失: {', '.join(missing)}")
                break
            
            if got_s != expected_s or got_cout != expected_cout:
                errors.append(
                    f"算法验证失败!\n输入: A={a:04X}, B={b:04X}, Cin={cin}\n"
                    f"预期: S={expected_s:04X}, Cout={expected_cout}\n"
                    f"实际: S={got_s:04X}, Cout={got_cout}"
                )
                break
        except Exception as e:
            errors.append(f"仿真运行时错误: {str(e)}")
            break
            
    if not errors:
        return {"success": True, "feedback": "真值表验证 100% 通过！"}
    else:
        return {"success": False, "feedback": "\n".join(errors)}

