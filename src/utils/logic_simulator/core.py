from typing import Dict, Any, List, Optional
from .registry import registry

class LogicSimulator:
    def __init__(self, logical: Any, project: Any):
        self.logical = logical
        self.project = project
        self.net_values: Dict[str, int] = {}
        self.prev_net_values: Dict[str, int] = {}
        self.sub_simulators: Dict[str, 'LogicSimulator'] = {}
        self.state: Dict[str, Any] = {}
        self.step_id = 0
        
        # Build component to nets mapping
        self.comp_nets: Dict[str, Dict[str, List[str]]] = {}
        for net in self.logical.nets:
            for ep in net.endpoints:
                if ep.instance not in self.comp_nets:
                    self.comp_nets[ep.instance] = {}
                if ep.port not in self.comp_nets[ep.instance]:
                    self.comp_nets[ep.instance][ep.port] = []
                if net.id not in self.comp_nets[ep.instance][ep.port]:
                    self.comp_nets[ep.instance][ep.port].append(net.id)

    def reset(self):
        """Full state reset for multi-run tests."""
        self.net_values = {}
        self.prev_net_values = {}
        self.state = {}
        self.step_id = 0
        for sub in self.sub_simulators.values():
            sub.reset()

    def set_input(self, label: str, value: int):
        """Helper to set an input value by pin/tunnel label."""
        found = False
        for instance in self.logical.instances:
            if instance.kind in ["Pin", "Button", "Clock"]:
                if instance.attrs.get("label") == label:
                    net_ids = self.comp_nets.get(instance.id, {}).get("io") or \
                              self.comp_nets.get(instance.id, {}).get("p0")
                    if net_ids:
                        self.net_values[net_ids[0]] = value
                        found = True
        
        for net in self.logical.nets:
            for lab in net.tunnel_labels:
                if lab == label:
                    self.net_values[net.id] = value
                    found = True
        return found

    def simulate(self, inputs: Dict[str, int], max_iterations: int = 2000, debug: bool = False) -> Dict[str, int]:
        self.prev_net_values = dict(self.net_values)
        self.step_id += 1

        # Inject inputs via labels (Instances)
        for instance in self.logical.instances:
            if instance.kind in ["Pin", "Button", "Clock"]:
                label = instance.attrs.get("label", "")
                if label in inputs:
                    net_ids = self.comp_nets.get(instance.id, {}).get("io") or \
                              self.comp_nets.get(instance.id, {}).get("p0")
                    if net_ids:
                        self.net_values[net_ids[0]] = inputs[label]

        # Inject inputs via Tunnel labels (Nets)
        for net in self.logical.nets:
            for label in net.tunnel_labels:
                if label in inputs:
                    self.net_values[net.id] = inputs[label]

        # Combinatorial settling
        for i in range(max_iterations):
            changed = False
            for instance in self.logical.instances:
                handler_cls = registry.get_handler(instance.kind)
                if handler_cls:
                    raw_nets = self.comp_nets.get(instance.id, {})
                    
                    if instance.kind == "Subcircuit":
                        # Subcircuits handle multi-bit net lists themselves
                        if handler_cls().evaluate(self, instance, raw_nets, debug=debug):
                            changed = True
                    else:
                        # Standard components expect single net IDs
                        flat_nets = {k: v[0] for k, v in raw_nets.items() if v}
                        if handler_cls().evaluate(self, instance, flat_nets, debug=debug):
                            changed = True
            if not changed:
                break
        else:
            if debug: print(f"WARNING: Did not settle in {max_iterations} iterations")

        # Collect outputs
        outputs = {}
        for instance in self.logical.instances:
            if instance.kind == "Pin" and instance.attrs.get("output") == "true":
                label = instance.attrs.get("label", "")
                net_ids = self.comp_nets.get(instance.id, {}).get("io") or \
                          self.comp_nets.get(instance.id, {}).get("p0")
                if net_ids and net_ids[0] in self.net_values:
                    outputs[label or instance.id] = self.net_values[net_ids[0]]
        return outputs
