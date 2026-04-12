import sys
import os

# Add src and vendor to path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), 'src/vendor'))

from src.utils.logic_simulator.core import LogicSimulator
from src.vendor.logisim_logic import load_project, extract_logical_circuit

def debug_stabilization():
    circ_path = 'tests/cases/simulator/test1.circ'
    proj = load_project(circ_path)
    main_circ = next(c for c in proj.circuits if '16位' in c.name)
    logical = extract_logical_circuit(main_circ, project=proj)
    
    sim = LogicSimulator(logical, proj)
    
    # Identify X, Y, Cin from the current test script behavior
    # Based on the user's log, net_9 is S.
    # I'll just use the inputs used in the test
    inputs = {
        'Pin_1_360_50': 0x1234, 
        'Pin_5_360_30': 0x5678, 
        'Pin_2_400_30': 0        
    }
    
    print("--- STARTING SIMULATION ---")
    # We want to see the last few iterations
    sim.simulate(inputs, max_iterations=2000, debug=True)

if __name__ == "__main__":
    debug_stabilization()
