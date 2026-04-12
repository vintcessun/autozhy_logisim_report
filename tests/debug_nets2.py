import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

from logisim_logic import load_project, extract_logical_circuit
from src.utils.logic_simulator import LogicSimulator

def show_nets(circuit_idx=5, radius=12):
    proj = load_project('tests/cases/simulator/test1.circ')
    circ = proj.circuits[circuit_idx]
    print(f"Circuit[{circuit_idx}]: {circ.name!r}")
    logical = extract_logical_circuit(circ, project=proj, radius=radius)
    
    print("\n--- Net Details ---")
    for net in logical.nets:
        tunnels = sorted(net.tunnel_labels)
        endpoints = [(e.instance.split('_')[0], e.port) for e in net.endpoints]
        print(f"  {net.id}: tunnels={tunnels} endpoints={endpoints}")

if __name__ == "__main__":
    ci = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    r = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    show_nets(ci, r)
