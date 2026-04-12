from typing import Dict, Any
from ..registry import ComponentHandler, registry

class PinHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        # Pins are handled primarily by the simulator's input/output collection logic in core.py
        # But we need the handler to satisfy the registry
        return False

class ButtonHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        # Button value is injected via simulator.net_values externally
        return False

class LEDHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        # LED just reflects the net value, already handled in core.py output collection
        return False

class ProbeHandler(ComponentHandler):
    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        # Probe provides local monitor
        return False

registry.register("Pin", PinHandler)
registry.register("Button", ButtonHandler)
registry.register("LED", LEDHandler)
registry.register("Clock", PinHandler) # Clock is like a Pin for simulation
registry.register("Probe", ProbeHandler)
