from typing import Dict, Any
from ..registry import ComponentHandler, registry

class SequentialHandler(ComponentHandler):
    """Base for sequential components that rely on clock edges."""
    def get_state(self, simulator: Any, instance_id: str, key: str, default: Any = 0) -> Any:
        full_key = f"{instance_id}_{key}"
        return simulator.state.get(full_key, default)

    def set_state(self, simulator: Any, instance_id: str, key: str, value: Any):
        full_key = f"{instance_id}_{key}"
        simulator.state[full_key] = value

class RegisterHandler(SequentialHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        width = int(instance.attrs.get("width") or 8)
        mask = (1 << width) - 1
        trigger = instance.attrs.get("trigger", "rising")
        
        # Logisim Register ports: in=p0, out=p1, en=p2, cp=p3, clr=p4
        in_net = nets.get("in") or nets.get("p0")
        out_net = nets.get("out") or nets.get("p1")
        en_net = nets.get("en") or nets.get("p2")
        cp_net = nets.get("cp") or nets.get("p3")
        clr_net = nets.get("clr") or nets.get("p4")
        
        changed = False
        current_val = self.get_state(simulator, instance.id, "value", 0)
        
        if clr_net and simulator.net_values.get(clr_net) == 1:
            if current_val != 0:
                self.set_state(simulator, instance.id, "value", 0)
                current_val = 0
                changed = True

        prev_cp = simulator.prev_net_values.get(cp_net, 0) if cp_net else 0
        curr_cp = simulator.net_values.get(cp_net, 0) if cp_net else 0
        
        is_triggered = False
        last_step = self.get_state(simulator, instance.id, "last_step", -1)
        if last_step < simulator.step_id and cp_net:
            if trigger == "rising" and prev_cp == 0 and curr_cp == 1:
                is_triggered = True
            elif trigger == "falling" and prev_cp == 1 and curr_cp == 0:
                is_triggered = True
            
            if is_triggered:
                self.set_state(simulator, instance.id, "last_step", simulator.step_id)
        
        if is_triggered:
            enabled = True
            if en_net is not None:
                enabled = (simulator.net_values.get(en_net, 0) == 1)
            
            if enabled:
                new_val = (simulator.net_values.get(in_net, 0) if in_net else 0) & mask
                if new_val != current_val:
                    self.set_state(simulator, instance.id, "value", new_val)
                    current_val = new_val
                    changed = True

        if out_net:
            if simulator.net_values.get(out_net) != current_val:
                simulator.net_values[out_net] = current_val
                changed = True
        return changed

class CounterHandler(SequentialHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        width = int(instance.attrs.get("width") or 8)
        max_val = (1 << width) - 1
        trigger = instance.attrs.get("trigger", "rising")
        
        # Logisim Counter ports: in=p0, out=p1, clk=p2, en=p3, clr=p4, load=p5...
        cp_net = nets.get("cp") or nets.get("p2")
        en_net = nets.get("en") or nets.get("p3")
        clr_net = nets.get("clr") or nets.get("p4")
        out_net = nets.get("out") or nets.get("p1")
        
        current_val = self.get_state(simulator, instance.id, "value", 0)
        changed = False
        
        if clr_net and simulator.net_values.get(clr_net) == 1:
            if current_val != 0:
                self.set_state(simulator, instance.id, "value", 0)
                current_val = 0
                changed = True

        prev_cp = simulator.prev_net_values.get(cp_net, 0) if cp_net else 0
        curr_cp = simulator.net_values.get(cp_net, 0) if cp_net else 0
        
        is_triggered = False
        last_step = self.get_state(simulator, instance.id, "last_step", -1)
        if last_step < simulator.step_id and cp_net:
            if trigger == "rising" and prev_cp == 0 and curr_cp == 1:
                is_triggered = True
            elif trigger == "falling" and prev_cp == 1 and curr_cp == 0:
                is_triggered = True
            
            if is_triggered:
                self.set_state(simulator, instance.id, "last_step", simulator.step_id)

        if is_triggered:
            enabled = True
            if en_net is not None:
                enabled = (simulator.net_values.get(en_net, 0) == 1)
            
            if enabled:
                current_val = (current_val + 1) & max_val
                self.set_state(simulator, instance.id, "value", current_val)
                changed = True

        if out_net:
            if simulator.net_values.get(out_net) != current_val:
                simulator.net_values[out_net] = current_val
                changed = True
        return changed

class DFlipFlopHandler(SequentialHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        trigger = instance.attrs.get("trigger", "rising")
        
        # D Flip-Flop ports: D=p0, clk=p1, Q=p2, ~Q=p3
        d_net = nets.get("D") or nets.get("p0")
        cp_net = nets.get("cp") or nets.get("p1")
        q_net = nets.get("Q") or nets.get("p2")
        nq_net = nets.get("~Q") or nets.get("p3")
        
        current_val = self.get_state(simulator, instance.id, "value", 0)
        changed = False
        
        prev_cp = simulator.prev_net_values.get(cp_net, 0) if cp_net else 0
        curr_cp = simulator.net_values.get(cp_net, 0) if cp_net else 0
        
        is_triggered = False
        last_step = self.get_state(simulator, instance.id, "last_step", -1)
        if last_step < simulator.step_id and cp_net:
            if trigger == "rising" and prev_cp == 0 and curr_cp == 1:
                is_triggered = True
            elif trigger == "falling" and prev_cp == 1 and curr_cp == 0:
                is_triggered = True
            
            if is_triggered:
                self.set_state(simulator, instance.id, "last_step", simulator.step_id)
        
        if is_triggered:
            new_val = (simulator.net_values.get(d_net, 0) if d_net else 0) & 1
            if new_val != current_val:
                self.set_state(simulator, instance.id, "value", new_val)
                current_val = new_val
                changed = True

        if q_net:
            if simulator.net_values.get(q_net) != current_val:
                simulator.net_values[q_net] = current_val
                changed = True
        if nq_net:
            new_nq = 1 - current_val
            if simulator.net_values.get(nq_net) != new_nq:
                simulator.net_values[nq_net] = new_nq
                changed = True
        return changed

registry.register("Register", RegisterHandler)
registry.register("Counter", CounterHandler)
registry.register("D Flip-Flop", DFlipFlopHandler)
