import sys
import random
from pathlib import Path

# Setup paths
project_root = Path(__file__).parents[1]
sys.path.append(str(project_root))
vendor_path = project_root / "src" / "vendor"
sys.path.insert(0, str(vendor_path))

from logisim_logic import load_project, extract_logical_circuit
from src.utils.logic_simulator import LogicSimulator

def run_grading(target_path: str, ref_path: str = "tests/cases/design/expected_result.circ") -> bool:
    """
    终极黑盒评分器。
    将 target_path 电路与 ref_path 参考电路进行 10+4 矩阵对撞。
    不向上层返回具体预期值，仅返回成功或失败。
    """
    print(f"\n===== [GRADING] Starting Final Validation vs {ref_path} =====")
    
    try:
        # Load DUT
        tp = Path(target_path)
        if not tp.exists(): raise FileNotFoundError("Target circuit not found.")
        proj_dut = load_project(str(tp))
        circ_dut = proj_dut.main_circuit if hasattr(proj_dut, 'main_circuit') and proj_dut.main_circuit else proj_dut.circuits[0]
        sim_dut = LogicSimulator(extract_logical_circuit(circ_dut, project=proj_dut))
        
        # Load Oracle (Reference)
        rp = Path(ref_path)
        if not rp.exists(): raise FileNotFoundError("Reference circuit not found.")
        proj_ref = load_project(str(rp))
        # 寻找 16 位主电路
        circ_ref = next(c for c in proj_ref.circuits if "16" in c.name or "C0" in [i.attrs.get('label') for i in extract_logical_circuit(c).instances if i.kind == 'Pin'])
        sim_ref = LogicSimulator(extract_logical_circuit(circ_ref, project=proj_ref))
        
        # Mappings
        # DUT: A, B, Cin -> S, Cout
        # REF: X, Y, C0 -> S, C16
        
        # Test Cases: 4 Boundary + 10 Random
        test_cases = []
        # 1. 0 + X
        test_cases.append((0, random.randint(0, 0xFFFF), random.randint(0, 1)))
        # 2. X + 0
        test_cases.append((random.randint(0, 0xFFFF), 0, random.randint(0, 1)))
        # 3. MAX + X
        test_cases.append((0xFFFF, random.randint(0, 0xFFFF), random.randint(0, 1)))
        # 4. X + MAX
        test_cases.append((random.randint(0, 0xFFFF), 0xFFFF, random.randint(0, 1)))
        # 5-14. Random
        for _ in range(10):
            test_cases.append((random.randint(0, 0xFFFF), random.randint(0, 0xFFFF), random.randint(0, 1)))
            
        failed_count = 0
        for i, (a, b, cin) in enumerate(test_cases):
            # Simulate DUT
            out_dut = sim_dut.simulate({"A": a, "B": b, "Cin": cin})
            # Simulate REF
            out_ref = sim_ref.simulate({"X": a, "Y": b, "C0": cin})
            
            s_dut, c_dut = out_dut.get("S"), out_dut.get("Cout")
            s_ref, c_ref = out_ref.get("S"), out_ref.get("C16")
            
            if s_dut != s_ref or c_dut != c_ref:
                print(f"[FAIL] Test {i+1}: Input A={a:04X}, B={b:04X}, Cin={cin}")
                # We do NOT print the expected vs got here because the DesignAgent shouldn't see it (though this is an external script)
                failed_count += 1
                
        if failed_count == 0:
            print("===== [RESULT] 100% SCORE! Functionally Identical to Reference. =====")
            return True
        else:
            print(f"===== [RESULT] FAILED with {failed_count} errors. =====")
            return False
            
    except Exception as e:
        print(f"[CRITICAL ERROR] Grading process failed: {e}")
        return False

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "tests/cases/design/16位快速加法器设计_design.circ"
    success = run_grading(target)
    sys.exit(0 if success else 1)
