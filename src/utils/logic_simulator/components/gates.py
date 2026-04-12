from typing import Dict, Any
import operator
from ..registry import ComponentHandler, registry

class GateHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        kind = instance.kind
        attrs = instance.attrs
        width = int(attrs.get("width") or 1)
        mask = (1 << width) - 1
        
        op_name = kind.split()[0]
        
        # Decide how many inputs to look for
        if op_name == "NOT":
            num_inputs = 1
        else:
            num_inputs = int(attrs.get("inputs") or 2)
            
        # Collect inputs
        gate_inputs = []
        for i in range(num_inputs):
            key = f"in{i}"
            net = nets.get(key)
            if net is None and i == 0:
                net = nets.get("in")
            
            # Default to 0 for floating inputs
            val = simulator.net_values.get(net, 0) if net else 0
            
            # Handle Negation (bubbles)
            if attrs.get(f"negate{i}") == "true":
                val = (~val) & mask
                
            gate_inputs.append(val)
            
        if not gate_inputs: return False
        
        # Compute result
        if op_name == "AND":
            res = gate_inputs[0]
            for v in gate_inputs[1:]: res &= v
        elif op_name == "OR":
            res = gate_inputs[0]
            for v in gate_inputs[1:]: res |= v
        elif op_name == "NAND":
            res = gate_inputs[0]
            for v in gate_inputs[1:]: res &= v
            res = (~res) & mask
        elif op_name == "NOR":
            res = gate_inputs[0]
            for v in gate_inputs[1:]: res |= v
            res = (~res) & mask
        elif op_name == "XOR":
            res = gate_inputs[0]
            for v in gate_inputs[1:]: res ^= v
        elif op_name == "XNOR":
            res = gate_inputs[0]
            for v in gate_inputs[1:]: res ^= v
            res = (~res) & mask
        elif op_name == "NOT":
            res = (~gate_inputs[0]) & mask
        else:
            if debug: print(f"[GATE] Unknown gate type: {kind}")
            return False
            
        out_net = nets.get("out") or nets.get("p1")
        if out_net:
            if simulator.net_values.get(out_net) != res:
                if debug: print(f"[GATE] {instance.id}({kind}) outputs {res} to {out_net}")
                simulator.net_values[out_net] = res
                return True
        return False

registry.register("AND Gate", GateHandler)
registry.register("OR Gate", GateHandler)
registry.register("NAND Gate", GateHandler)
registry.register("NOR Gate", GateHandler)
registry.register("XOR Gate", GateHandler)
registry.register("XNOR Gate", GateHandler)
registry.register("NOT Gate", GateHandler)
