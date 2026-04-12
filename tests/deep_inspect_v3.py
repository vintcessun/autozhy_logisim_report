import xml.etree.ElementTree as ET
import sys
import os

# Set encoding for output to handle Chinese characters on Windows
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def deep_inspect(path):
    print(f"\n=== DEEP INSPECTION: {path} ===")
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        
        circuit_names = [c.get('name') for c in root.findall('.//circuit')]
        print(f"Circuits found: {circuit_names}")
        
        for i, circ in enumerate(root.findall('.//circuit')):
            c_name = circ.get('name')
            print(f"\n[{i}] {repr(c_name)}:")
            
            comp_types = {}
            for comp in circ.findall('comp'):
                kind = comp.get('name')
                lib = comp.get('lib')
                comp_types[kind] = comp_types.get(kind, 0) + 1
            
            for kind, count in sorted(comp_types.items()):
                print(f"  - {kind}: {count}")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    deep_inspect('tests/cases/simulator/test1.circ')
    deep_inspect('tests/cases/simulator/test2.circ')
