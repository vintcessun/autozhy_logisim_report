import xml.etree.ElementTree as ET
import sys
import os

# Add vendor to path to load project
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))
from logisim_logic import load_project

def deep_inspect(path):
    print(f"\n=== DEEP INSPECTION: {path} ===")
    proj = load_project(path)
    
    for i, circ in enumerate(proj.circuits):
        print(f"\n[{i}] {repr(circ.name)}:")
        labels = [comp.get('label') for comp in circ.components if comp.get('label')]
        print(f"  Labels: {labels}")
        if 'X' in labels and 'Y' in labels and 'S' in labels:
            print(f"  *** MATCH CONTAINS X, Y, S ***")
        
        subcircs = [comp.name for comp in circ.components if len(comp.name) > 10 or proj.has_circuit(comp.name)]
        if subcircs:
            print(f"  Subcircuits: {set(subcircs)}")

if __name__ == "__main__":
    deep_inspect('tests/cases/simulator/test1.circ')
    deep_inspect('tests/cases/simulator/test2.circ')
