from typing import Dict, Any
from ..registry import ComponentHandler, registry

class MultiplexerHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        attrs = instance.attrs
        select_width = int(attrs.get("select") or 1)
        data_width = int(attrs.get("width") or 1)
        mask = (1 << data_width) - 1
        
        sel_net = nets.get("select")
        if sel_net is None: return False
        
        enable_net = nets.get("enable")
        if enable_net is not None:
            # If enabled is low, output is 0 (simplified)
            if simulator.net_values.get(enable_net, 1) == 0:
                out_net = nets.get("out")
                if out_net and simulator.net_values.get(out_net) != 0:
                    simulator.net_values[out_net] = 0
                    return True
                return False

        sel_val = simulator.net_values.get(sel_net, 0)
        in_net_key = f"in{sel_val}"
        in_net = nets.get(in_net_key)
        
        # If the specific input is not set, default to 0
        new_val = (simulator.net_values.get(in_net, 0) if in_net else 0) & mask
        
        out_net = nets.get("out")
        if out_net:
            if simulator.net_values.get(out_net) != new_val:
                simulator.net_values[out_net] = new_val
                return True
        return False

class DemultiplexerHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        attrs = instance.attrs
        select_width = int(attrs.get("select") or 1)
        fanout = 1 << select_width
        
        sel_net = nets.get("select")
        in_net = nets.get("in")
        
        if sel_net is None or in_net is None: return False
        
        sel_val = simulator.net_values.get(sel_net, 0)
        in_val = simulator.net_values.get(in_net, 0)
        
        changed = False
        for i in range(fanout):
            out_net = nets.get(f"out{i}")
            if out_net:
                new_out_val = in_val if i == sel_val else 0
                if simulator.net_values.get(out_net) != new_out_val:
                    simulator.net_values[out_net] = new_out_val
                    changed = True
        return changed

registry.register("Multiplexer", MultiplexerHandler)
registry.register("Demultiplexer", DemultiplexerHandler)
