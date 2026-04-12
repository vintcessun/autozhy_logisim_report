import sys
import os

# Add src and vendor to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

from logisim_logic import load_project, extract_logical_circuit

def verify(path):
    print(f"\n--- Verifying {path} ---")
    proj = load_project(path)
    
    # Check "4位并行加法器1"
    target_name = "4位并行加法器1"
    if proj.has_circuit(target_name):
        print(f"Found {target_name}")
        target = proj.circuit(target_name)
        ports = target.port_offsets()
        print(f"  Ports: {[p.name for p in ports]}")
    else:
        print(f"NOT FOUND: {target_name}")
        print(f"Available circuits: {[c.name for c in proj.circuits]}")

if __name__ == "__main__":
    verify('tests/cases/simulator/test1.circ')
