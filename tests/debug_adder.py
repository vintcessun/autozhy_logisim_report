import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

from logisim_logic import load_project, extract_logical_circuit
from src.utils.logic_simulator import LogicSimulator

def diag_adder(circuit_idx=5, radius=12):
    proj = load_project('tests/cases/simulator/test1.circ')
    circ = proj.circuits[circuit_idx]
    print(f"Circuit[{circuit_idx}]: {circ.name!r}")
    logical = extract_logical_circuit(circ, project=proj, radius=radius)
    
    sim = LogicSimulator(logical, project=proj)
    
    print(f"\nTotal instances: {len(logical.instances)}")
    print(f"Total nets: {len(logical.nets)}")
    
    print("\n--- Pin Net Mapping ---")
    for inst in logical.instances:
        if inst.kind == 'Pin':
            nets = sim.comp_nets.get(inst.id, {})
            label = inst.attrs.get("label", "?")
            out = inst.attrs.get("output", "?")
            print(f"  Pin '{label}' (out={out}) id={inst.id}: nets={nets}")
    
    print("\n--- Instances without any connected nets ---")
    disconnected = [i for i in logical.instances if i.id not in sim.comp_nets]
    for inst in disconnected:
        print(f"  {inst.kind} id={inst.id}")

    print("\n--- Running simulation ---")
    outputs = sim.simulate({'X': 0x1234, 'Y': 0x5678, 'C0': 0}, debug=False)
    print(f"Results: S={outputs.get('S')}, C16={outputs.get('C16')}, P*={outputs.get('P*')}")
    if not outputs:
        print("Net values after simulate:", dict(list(sim.net_values.items())[:20]))
    else:
        S = outputs.get('S')
        if S is not None:
            print(f"  S as hex: {S:#06x}")

if __name__ == "__main__":
    ci = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    r = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    diag_adder(ci, r)
