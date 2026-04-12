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
from src.utils.logic_simulator.agent import CircuitAgent

def test_8bit_multiplier_agent():
    proj = load_project('tests/cases/simulator/test2.circ')
    circ = proj.circuits[0]
    assert circ is not None, "Could not find target circuit"
    
    logical = extract_logical_circuit(circ, project=proj)
    sim = LogicSimulator(logical, project=proj)
    agent = CircuitAgent(sim)
    
    clk_label = "CLK"
    reset_label = "λ" # λ (Reset)
    
    test_data = [
        (3, 5, 15),
        (12, 10, 120),
    ]
    
    for a, b, expected in test_data:
        # Reset the simulator state between runs
        sim.reset()
        
        # 1. Set Inputs
        sim.set_input("A", a)
        sim.set_input("B", b)
        
        # 2. Pulse Reset (λ)
        agent.pulse(reset_label)
        
        # 3. Target LED at (680, 50) based on diagnostic research
        end_id = next((i.id for i in logical.instances if i.kind == 'LED' and (str(i.loc) == '(680, 50)' or i.attrs.get('label') == 'END')), None)
        
        def is_done():
            if end_id:
                 net_id = sim.comp_nets.get(end_id, {}).get('io')
                 if net_id and sim.net_values.get(net_id) == 1:
                     return True
            # Fallback
            for inst in logical.instances:
                if inst.kind == 'LED':
                    net_id = sim.comp_nets.get(inst.id, {}).get('io')
                    if net_id and sim.net_values.get(net_id) == 1:
                        return True
            return False
            
        final_outputs = agent.run_until(clk_label, is_done, max_cycles=3000, debug=True)
        actual = final_outputs.get("Product", 0)
        print(f"DEBUG {a} * {b} = {actual} (expected {expected})")
        assert actual == expected

if __name__ == "__main__":
    pytest.main([__file__])
