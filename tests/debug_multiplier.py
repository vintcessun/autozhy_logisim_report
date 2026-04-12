import sys
import os

# Add src and vendor to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

from src.utils.logic_simulator.core import LogicSimulator
from src.utils.logic_simulator.agent import CircuitAgent
from src.vendor.logisim_logic import load_project, extract_logical_circuit

def debug_multiplier():
    circ_path = 'tests/cases/simulator/test2.circ'
    proj = load_project(circ_path)
    main_circ = proj.circuits[0] 
    logical = extract_logical_circuit(main_circ, project=proj)
    
    sim = LogicSimulator(logical, proj)
    agent = CircuitAgent(sim)
    
    x_label = 'X'
    y_label = 'Y'
    
    # Identify reset button
    reset_label = next(i.attrs.get('label') for i in logical.instances 
                       if i.kind == 'Button' and i.attrs.get('label') != 'CLK')
    
    print("--- STEP 1: INITIAL INPUTS ---")
    sim.simulate({x_label: 3, y_label: 5}, debug=True, max_iterations=10)
    
    print("\n--- STEP 2: PULSE RESET (HIGH) ---")
    sim.simulate({reset_label: 1}, debug=True, max_iterations=10)
    
    print("\n--- STEP 3: PULSE RESET (LOW) ---")
    sim.simulate({reset_label: 0}, debug=True, max_iterations=10)
    
    print("\n--- STEP 4: CLOCK 1 (HIGH) ---")
    sim.simulate({'CLK': 1}, debug=True, max_iterations=10)

if __name__ == "__main__":
    debug_multiplier()
