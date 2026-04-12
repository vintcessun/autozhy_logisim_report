import pytest
import sys
import os
from pathlib import Path

# Add project root to path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.append(str(root / "src" / "vendor"))

from logisim_logic import load_project, extract_logical_circuit
from src.utils.logic_simulator.core import LogicSimulator

def test_16bit_fast_adder():
    proj = load_project('tests/cases/simulator/test1.circ')
    # Use Index 4: 16-bit Serial Fast Adder 2
    # radius=12 for balanced extraction precision
    logical = extract_logical_circuit(proj.circuits[4], project=proj, radius=12)
    sim = LogicSimulator(logical, project=proj)
    
    test_cases = [
        (0x1234, 0x5678, 0, 0x68AC, 0),
        (0xFFFF, 0x0001, 0, 0x0000, 1),
    ]
    
    for a, b, cin, exp_s, exp_cout in test_cases:
        outputs = sim.simulate({"X": a, "Y": b, "C0": cin})
        print(f"DEBUG {a:#x} + {b:#x} + {cin} -> {outputs}")
        
        s_val = outputs.get("S", 0)
        cout_val = outputs.get("C16", 0)
        print(f"DEBUG Actual: S={s_val:#x}, C16={cout_val}")
        
        assert s_val == exp_s
        assert cout_val == exp_cout

if __name__ == "__main__":
    pytest.main([__file__])
