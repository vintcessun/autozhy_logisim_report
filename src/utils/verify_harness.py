import sys
from pathlib import Path
from src.utils.logic_simulator import LogicSimulator
import random

# Ensure vendor path
vendor_path = str(Path(__file__).parents[1] / "src" / "vendor")
if vendor_path not in sys.path:
    sys.path.insert(0, vendor_path)

from logisim_logic import load_project, extract_logical_circuit

def verify_adder(circ_path: str, num_tests=100) -> dict:
    """
    Verify a 16-bit CLA circuit.
    """
    proj = load_project(circ_path)
    # Target the main circuit
    main_name = proj.main.name if proj.main else None
    if main_name and any(c.name == main_name for c in proj.circuits):
        circuit = next(c for c in proj.circuits if c.name == main_name)
    else:
        circuit = proj.circuits[0]
    logical = extract_logical_circuit(circuit, project=proj)
    sim = LogicSimulator(logical)
    
    results = {
        "passed": 0,
        "failed": 0,
        "errors": []
    }
    
    # Test vectors
    test_cases = [
        (0, 0, 0),
        (0xFFFF, 0, 0),
        (0xFFFF, 1, 0),
        (0x7FFF, 0x8001, 0),
        (0xFFFF, 0xFFFF, 1),
    ]
    # Add random cases
    for _ in range(num_tests):
        test_cases.append((
            random.randint(0, 0xFFFF),
            random.randint(0, 0xFFFF),
            random.randint(0, 1)
        ))
        
    for a, b, cin in test_cases:
        inputs = {"A": a, "B": b, "Cin": cin}
        try:
            outputs = sim.simulate(inputs)
            
            # Expected
            total = a + b + cin
            expected_s = total & 0xFFFF
            expected_cout = (total >> 16) & 1
            
            got_s = outputs.get("S", -1)
            got_cout = outputs.get("Cout", -1)
            
            if got_s == expected_s and got_cout == expected_cout:
                results["passed"] += 1
            else:
                results["failed"] += 1
                msg = f"Match Failed: A={a:04X}, B={b:04X}, Cin={cin} | Expected: S={expected_s:04X}, Cout={expected_cout} | Got: S={got_s:04X}, Cout={got_cout}"
                results["errors"].append(msg)
                if len(results["errors"]) > 5: break # Only show first 5
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"Simulation Error: {str(e)}")
            break
            
    return results

if __name__ == "__main__":
    # Test on the last generated one
    path = "tests/cases/design/16位快速加法器设计_design.circ"
    if Path(path).exists():
        res = verify_adder(path)
        print(f"Verification Results: {res['passed']} Passed, {res['failed']} Failed")
        for err in res["errors"]:
            print(f"  [ERROR] {err}")
    else:
        print(f"File not found: {path}")
