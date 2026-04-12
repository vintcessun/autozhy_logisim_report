import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/vendor')))

import xml.etree.ElementTree as ET

def dump_pins_only(path, circuit_index):
    tree = ET.parse(path)
    root = tree.getroot()
    for ci, circuit in enumerate(root.findall('.//circuit')):
        if ci != circuit_index:
            continue
        cname = circuit.get('name', '')
        print(f"=== Circuit[{ci}]: {cname!r} ===")
        for comp in circuit.findall('comp'):
            name = comp.get('name', '')
            if name not in ('Pin', 'Button', 'LED', 'Clock'):
                continue
            loc = comp.get('loc', '')
            attrs = {}
            for a in comp.findall('a'):
                attrs[a.get('name', '')] = a.get('val', a.text or '')
            label = attrs.get('label', '')
            output = attrs.get('output', '')
            width = attrs.get('width', '1')
            ishex = label.encode('utf-8').hex()
            print(f"  {name} at {loc}: label={label!r} (hex:{ishex}) out={output!r} w={width!r}")

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else 'tests/cases/simulator/test1.circ'
    ci = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    dump_pins_only(path, ci)
