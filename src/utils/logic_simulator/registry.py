from typing import Dict, Any, Type, Optional

class ComponentHandler:
    """Base class for handling a specific type of Logisim component."""
    def __init__(self):
        pass

    def evaluate(self, simulator: Any, instance: Any, nets: Dict[str, str], debug: bool = False) -> bool:
        """
        Evaluate the instance logic.
        Returns: True if any output net value changed.
        """
        raise NotImplementedError

class HandlerRegistry:
    """Registry for component handlers."""
    def __init__(self):
        self.handlers: Dict[str, ComponentHandler] = {}
        self.type_map: Dict[str, Type[ComponentHandler]] = {}

    def register(self, kind: str, handler_cls: Type[ComponentHandler]):
        print(f"[REG] Registering {kind}")
        self.type_map[kind] = handler_cls

    def get_handler(self, kind: str) -> Optional[Type[ComponentHandler]]:
        return self.type_map.get(kind)

registry = HandlerRegistry()
