from typing import Dict, Any, List
import time

class CircuitAgent:
    """Agent to drive logic simulations with timed inputs (clock, buttons)."""
    def __init__(self, simulator: Any):
        self.simulator = simulator
        
    def set_inputs(self, values: Dict[str, int]):
        """Set steady-state inputs."""
        for k, v in values.items():
            self.simulator.net_values[k] = v # This is a bit direct, but simulate() will handle it.
            
    def pulse(self, label: str, duration_sec: float = 0):
        """Pulse a button or signal (High then Low)."""
        # Set to 1
        self.simulator.simulate({label: 1}, max_iterations=2000)
        if duration_sec > 0: time.sleep(duration_sec)
        # Set to 0
        self.simulator.simulate({label: 0}, max_iterations=2000)
        
    def run_until(self, clk_label: str, stop_condition: Any, max_cycles: int = 100, debug: bool = False) -> Dict[str, Any]:
        """
        Clock the circuit until a stop condition (callable or net label) is met.
        Returns final outputs.
        """
        def check():
            if callable(stop_condition):
                return stop_condition()
            return self.simulator.net_values.get(stop_condition) == 1

        for i in range(max_cycles):
            # CLK High
            if debug: print(f"[AGENT] Cycle {i} - CLK High")
            res_high = self.simulator.simulate({clk_label: 1}, max_iterations=2000)
            if res_high and check():
                return res_high
            
            # CLK Low
            if debug: print(f"[AGENT] Cycle {i} - CLK Low")
            res_low = self.simulator.simulate({clk_label: 0}, max_iterations=2000)
            if res_low and check():
                return res_low
                
        raise TimeoutError(f"Circuit did not reach stop condition after {max_cycles} cycles")
