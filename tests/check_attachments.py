import sys
import os

# Add src and vendor to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

from logisim_logic import load_project, extract_logical_circuit

def check(path, circuit_idx=5):
    print(f"\n--- Checking {path} (Circuit {circuit_idx}) ---")
    proj = load_project(path)
    circ = proj.circuits[circuit_idx]
    logical = extract_logical_circuit(circ, project=proj)
    
    subcircs = [i for i in logical.instances if i.kind not in ['Pin', 'Splitter', 'Tunnel', 'Wire', 'Power', 'Ground', 'Constant', 'Text', 'Probe']]
    print(f"Number of subcircuits: {len(subcircs)}")
    
    for i in subcircs:
        print(f"Subcircuit {i.id} ({i.kind}):")
        for port, pt in i.port_points.items():
            # Check if this point is in any net
            found_net = None
            for net in logical.nets:
                if pt in net.points:
                    found_net = net.id
                    break
            print(f"  Port {port} at {pt}: Net = {found_net}")

if __name__ == "__main__":
    check('tests/cases/simulator/test1.circ', 5)
