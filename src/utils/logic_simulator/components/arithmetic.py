from typing import Dict, Any
from ..registry import ComponentHandler, registry

class ComparatorHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        attrs = instance.attrs
        width = int(attrs.get("width") or 8)
        mode = attrs.get("mode", "unsigned")
        
        a_net = nets.get("A")
        b_net = nets.get("B")
        if a_net is None or b_net is None: return False
        
        a_val = simulator.net_values.get(a_net, 0)
        b_val = simulator.net_values.get(b_net, 0)
        
        if mode == "signed":
            # Convert to signed
            if a_val & (1 << (width - 1)): a_val -= (1 << width)
            if b_val & (1 << (width - 1)): b_val -= (1 << width)
            
        changed = False
        res = {
            "gt": 1 if a_val > b_val else 0,
            "eq": 1 if a_val == b_val else 0,
            "lt": 1 if a_val < b_val else 0
        }
        
        for port, val in res.items():
            out_net = nets.get(port)
            if out_net and simulator.net_values.get(out_net) != val:
                simulator.net_values[out_net] = val
                changed = True
        return changed

class AdderHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        width = int(instance.attrs.get("width") or 8)
        mask = (1 << width) - 1
        
        a_val = simulator.net_values.get(nets.get("A"), 0) if nets.get("A") else 0
        b_val = simulator.net_values.get(nets.get("B"), 0) if nets.get("B") else 0
        cin = simulator.net_values.get(nets.get("cin"), 0) if nets.get("cin") else 0
        
        sum_val = a_val + b_val + cin
        res = sum_val & mask
        cout = 1 if sum_val > mask else 0
        
        changed = False
        out_net = nets.get("out")
        if out_net:
            if simulator.net_values.get(out_net) != res:
                simulator.net_values[out_net] = res
                changed = True
        
        cout_net = nets.get("cout")
        if cout_net:
            if simulator.net_values.get(cout_net) != cout:
                simulator.net_values[cout_net] = cout
                changed = True
        return changed

class MultiplierHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        width = int(instance.attrs.get("width") or 8)
        mask = (1 << width) - 1
        
        a_val = simulator.net_values.get(nets.get("A"), 0) if nets.get("A") else 0
        b_val = simulator.net_values.get(nets.get("B"), 0) if nets.get("B") else 0
        cin = simulator.net_values.get(nets.get("cin"), 0) if nets.get("cin") else 0
        
        prod_val = a_val * b_val + cin
        res = prod_val & mask
        cout = (prod_val >> width) & mask # For multipliers, cout is the high part
        
        changed = False
        out_net = nets.get("out")
        if out_net:
            if simulator.net_values.get(out_net) != res:
                simulator.net_values[out_net] = res
                changed = True
        
        cout_net = nets.get("cout")
        if cout_net:
            if simulator.net_values.get(cout_net) != cout:
                simulator.net_values[cout_net] = cout
                changed = True
        return changed

class NegatorHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        width = int(instance.attrs.get("width") or 8)
        mask = (1 << width) - 1
        in_net = nets.get("in") or nets.get("p0")
        out_net = nets.get("out") or nets.get("p1")
        
        if not out_net: return False
        
        in_val = simulator.net_values.get(in_net, 0) if in_net else 0
        new_val = ((-in_val) & mask)
        
        if simulator.net_values.get(out_net) != new_val:
            simulator.net_values[out_net] = new_val
            return True
        return False

registry.register("Comparator", ComparatorHandler)
registry.register("Adder", AdderHandler)
registry.register("Multiplier", MultiplierHandler)
registry.register("Negator", NegatorHandler)
