import xml.etree.ElementTree as ET
import sys

def inspect(path):
    print(f"\n--- Inspecting {path} ---")
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        
        print("Libraries:")
        for lib in root.findall('lib'):
            name = lib.get('name')
            desc = lib.get('desc')
            print(f"  name={name}, desc={desc}")
            
        print("\nCircuits:")
        for i, circ in enumerate(root.findall('circuit')):
            name = circ.get('name')
            print(f"  [{i}] {name}")
            subcircs = []
            for comp in circ.findall('comp'):
                c_name = comp.get('name')
                c_lib = comp.get('lib')
                if c_lib or (c_name and len(c_name) > 10): # heuristic for subcircs
                    subcircs.append((c_name, c_lib))
            if subcircs:
                print(f"    Possible subcircuits: {set(subcircs)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect('tests/cases/simulator/test1.circ')
    inspect('tests/cases/simulator/test2.circ')
