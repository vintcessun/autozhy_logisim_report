import sys
import os

# Add src and vendor to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

from logisim_logic import load_project, extract_logical_circuit

def dump_circuit(path):
    print(f"\n=== {path} ===")
    proj = load_project(path)
    for i, circ in enumerate(proj.circuits):
        print(f"[{i}] Circuit: {circ.name}")
        pins = [c for c in circ.components if c.name == 'Pin']
        leds = [c for c in circ.components if c.name == 'LED']
        buttons = [c for c in circ.components if c.name == 'Button']
        clocks = [c for c in circ.components if c.name == 'Clock']
        
        for p in pins:
            print(f"  Pin at {p.loc}: {p.attr_map()}")
        for l in leds:
            print(f"  LED at {l.loc}: {l.attr_map()}")
        for b in buttons:
            print(f"  Button at {b.loc}: {b.attr_map()}")
        for c in clocks:
            print(f"  Clock at {c.loc}: {c.attr_map()}")

if __name__ == "__main__":
    dump_circuit('tests/cases/simulator/test1.circ')
    dump_circuit('tests/cases/simulator/test2.circ')
