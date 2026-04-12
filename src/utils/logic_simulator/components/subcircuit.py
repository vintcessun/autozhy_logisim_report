from typing import Dict, Any, List, Optional
from ..registry import ComponentHandler, registry
import copy

class SubcircuitHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, List[str]], debug: bool = False) -> bool:
        kind = instance.kind
        if not simulator.project.has_circuit(kind):
            return False
            
        # Get or create sub-simulator for this instance
        sub_sim = simulator.sub_simulators.get(instance.id)
        if sub_sim is None:
            from ..core import LogicSimulator
            target_raw = simulator.project.circuit(kind)
            from src.vendor.logisim_logic import extract_logical_circuit
            logical = extract_logical_circuit(target_raw, project=simulator.project, radius=12)
            sub_sim = LogicSimulator(logical, simulator.project)
            simulator.sub_simulators[instance.id] = sub_sim
            
            # Sub-circuit maps: Loc -> NetID and Label -> [NetIDs]
            sub_sim.port_nets = {}
            sub_sim.port_labels = {} # label -> list of net_ids
            
            for net in sub_sim.logical.nets:
                for pt in net.points:
                    sub_sim.port_nets[pt] = net.id
            
            # Sort internal pins by location to ensure stable bus mapping
            sub_pins = sorted([i for i in sub_sim.logical.instances if i.kind == "Pin"], key=lambda i: (i.loc[1], i.loc[0]))
            for p in sub_pins:
                label = p.attrs.get("label")
                if label:
                    if label not in sub_sim.port_labels:
                        sub_sim.port_labels[label] = []
                    p_nets = sub_sim.comp_nets.get(p.id, {})
                    n_list = p_nets.get("io") or p_nets.get("p0")
                    if n_list:
                        sub_sim.port_labels[label].append(n_list[0])

        def sub_normalize(n: str) -> str:
            if not n: return ""
            n = str(n).split('（')[0].split('(')[0].strip().replace('_', '').lower()
            if n in ['ci', 'c0', 'cin_']: return 'cin'
            if n in ['co', 'c16', 'cout_']: return 'cout'
            return n

        # 1. Map parent inputs -> child
        for port_name, parent_net_ids in nets.items():
            port_meta = instance.port_info.get(port_name)
            if not port_meta or port_meta.get("direction") != "input":
                continue
                
            # Try positional matching first (most robust for Logisim boxes)
            loc = port_meta.get("pin_loc")
            sub_nid = sub_sim.port_nets.get(loc)
            if sub_nid:
                # Map first net in parent bus to this pin
                val = simulator.net_values.get(parent_net_ids[0], 0)
                sub_sim.net_values[sub_nid] = val
                sub_sim.prev_net_values[sub_nid] = simulator.prev_net_values.get(parent_net_ids[0], 0)
            else:
                # Fallback to label-based bus mapping
                target_label = port_meta.get("label") or port_name
                sub_nids = sub_sim.port_labels.get(target_label)
                if sub_nids:
                    # Map bit-by-bit if sizes match
                    for i in range(min(len(parent_net_ids), len(sub_nids))):
                        val = simulator.net_values.get(parent_net_ids[i], 0)
                        sub_sim.net_values[sub_nids[i]] = val
                        sub_sim.prev_net_values[sub_nids[i]] = simulator.prev_net_values.get(parent_net_ids[i], 0)
        
        # 2. Simulate subcircuit
        sub_sim.step_id = simulator.step_id
        sub_sim.simulate({}, max_iterations=300, debug=debug)
        
        # 3. Map child outputs -> parent
        changed = False
        for port_name, parent_net_ids in nets.items():
            port_meta = instance.port_info.get(port_name)
            if not port_meta or port_meta.get("direction") != "output":
                continue
                
            loc = port_meta.get("pin_loc")
            sub_nid = sub_sim.port_nets.get(loc)
            if sub_nid:
                val = sub_sim.net_values.get(sub_nid, 0)
                if simulator.net_values.get(parent_net_ids[0]) != val:
                    simulator.net_values[parent_net_ids[0]] = val
                    changed = True
            else:
                target_label = port_meta.get("label") or port_name
                sub_nids = sub_sim.port_labels.get(target_label)
                if sub_nids:
                    for i in range(min(len(parent_net_ids), len(sub_nids))):
                        val = sub_sim.net_values.get(sub_nids[i], 0)
                        if simulator.net_values.get(parent_net_ids[i]) != val:
                            simulator.net_values[parent_net_ids[i]] = val
                            changed = True
        return changed

registry.register("Subcircuit", SubcircuitHandler)
