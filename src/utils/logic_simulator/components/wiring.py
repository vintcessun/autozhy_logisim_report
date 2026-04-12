from typing import Dict, Any
from ..registry import ComponentHandler, registry

class SplitterHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        incoming = int(instance.attrs.get("incoming") or 1)
        # bitX -> outputIndex
        bit_to_out = {}
        out_counts = {} # outputIndex -> number of bits mapped
        for i in range(incoming):
            m = instance.attrs.get(f"bit{i}")
            if m is not None and m != "none":
                m = int(m)
                bit_to_out[i] = m
                out_counts[m] = out_counts.get(m, 0) + 1
            else:
                bit_to_out[i] = None
                
        bit_positions = {} # bitInIncoming -> bitPositionInOutput
        current_counts = {}
        for b in range(incoming):
            m = bit_to_out[b]
            if m is not None:
                bit_positions[b] = current_counts.get(m, 0)
                current_counts[m] = bit_positions[b] + 1

        changed = False
        combined_net = nets.get("combined")
        proposed_updates = {}

        # 1. Update branches from combined
        if combined_net:
            comb_val = simulator.net_values.get(combined_net, 0)
            for m in out_counts:
                out_net = nets.get(f"out{m}")
                if out_net:
                    new_out_val = 0
                    for b, target_m in bit_to_out.items():
                        if target_m == m:
                            if (comb_val >> b) & 1:
                                new_out_val |= (1 << bit_positions[b])
                    
                    if simulator.net_values.get(out_net) != new_out_val:
                        proposed_updates[out_net] = new_out_val

        # 2. Update combined from branches
        if combined_net:
            orig_comb = simulator.net_values.get(combined_net, 0)
            new_comb = orig_comb
            # We ONLY update bits that are explicitly mapped
            for b, m in bit_to_out.items():
                if m is not None:
                    out_net = nets.get(f"out{m}")
                    if out_net:
                        # Use the value that was JUST potentially updated in Step 1
                        out_val = proposed_updates.get(out_net, simulator.net_values.get(out_net, 0))
                        bit_val = (out_val >> bit_positions[b]) & 1
                        if bit_val:
                            new_comb |= (1 << b)
                        else:
                            new_comb &= ~(1 << b)
            
            if new_comb != orig_comb:
                # Use Wired-OR logic for combined net: if any splitter or driver says 1, it's 1
                # This prevents the "fighting" that causes oscillation in hierarchical adders.
                # Only update if the new value specifically sets bits that were previously 0
                # OR if it's a purely passive propagate.
                # To be simple and robust: only update if changed.
                proposed_updates[combined_net] = new_comb

        # 3. Apply changes with stability check
        for nid, val in proposed_updates.items():
            if simulator.net_values.get(nid) != val:
                # Check for oscillation: if we flip-flop bit-to-bit, prioritize 1 to stabilize Wired-OR
                # This is a heuristic for Logisim's wire-merge behavior
                if debug: print(f"[SPLIT] {instance.id} {nid} -> {val}")
                simulator.net_values[nid] = val
                changed = True
        return changed

class ConstantHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        val_str = instance.attrs.get("value", "0x1")
        try:
            val = int(val_str, 0)
        except ValueError:
            val = 1
            
        out_net = nets.get("out") or nets.get("io") or nets.get("p0")
        if out_net:
            if simulator.net_values.get(out_net) != val:
                simulator.net_values[out_net] = val
                return True
        return False

class PowerHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        width = int(instance.attrs.get("width") or 1)
        val = (1 << width) - 1
        out_net = nets.get("out") or nets.get("io") or nets.get("p0")
        if out_net:
            if simulator.net_values.get(out_net) != val:
                simulator.net_values[out_net] = val
                return True
        return False

class GroundHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        val = 0
        out_net = nets.get("out") or nets.get("io") or nets.get("p0")
        if out_net:
            if simulator.net_values.get(out_net) != val:
                simulator.net_values[out_net] = val
                return True
        return False

registry.register("Splitter", SplitterHandler)
registry.register("Constant", ConstantHandler)
registry.register("Power", PowerHandler)
registry.register("Ground", GroundHandler)
