import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

from logisim_logic import load_project, extract_logical_circuit
from src.utils.logic_simulator import LogicSimulator

def diag_adder():
    proj = load_project('tests/cases/simulator/test1.circ')
    circ = proj.circuits[5]
    print(f"Circuit: {circ.name}")
    logical = extract_logical_circuit(circ, project=proj)
    
    print(f"Total instances: {len(logical.instances)}")
    print(f"Total nets: {len(logical.nets)}")
    
    sim = LogicSimulator(logical, project=proj)
    
    print("\n--- Pin Net Mapping ---")
    for inst in logical.instances:
        if inst.kind == 'Pin':
            nets = sim.comp_nets.get(inst.id, {})
            label = inst.attrs.get("label", "?")
            output = inst.attrs.get("output", "false")
            print(f"  Pin '{label}' (output={output}) id={inst.id}: nets={nets}")
    
    print("\n--- Subcircuit instances ---")
    for inst in logical.instances:
        if inst.kind not in ['Pin', 'Splitter', 'Tunnel', 'Wire', 'Power', 'Ground', 'Constant']:
            nets = sim.comp_nets.get(inst.id, {})
            print(f"  {inst.kind} id={inst.id}: nets={nets}")
    
    print("\n--- Running simulation ---")
    outputs = sim.simulate({'X': 0x1234, 'Y': 0x5678, 'C0': 0}, debug=True)
    print(f"\nOutputs: {outputs}")
    print(f"Net values (all): {sim.net_values}")


def diag_multiplier():
    proj = load_project('tests/cases/simulator/test2.circ')
    circ = proj.circuits[0]
    print(f"\n=== Multiplier Circuit: {circ.name} ===")
    logical = extract_logical_circuit(circ, project=proj)
    
    print("Instances:")
    for inst in logical.instances:
        label = inst.attrs.get("label", "")
        width = inst.attrs.get("width", "1")
        output = inst.attrs.get("output", "")
        print(f"  {inst.kind} '{label}' (output={output}, width={width}) id={inst.id}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'mult':
        diag_multiplier()
    else:
        diag_adder()
