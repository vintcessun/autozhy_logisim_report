import asyncio
import pytest
import sys
import os
from pathlib import Path

# Add project root to path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from src.utils.config_loader import ConfigManager
from src.utils.sim_runner import LogisimEmulator

async def run_adder_test(config):
    emulator = LogisimEmulator(config, None)
    circ_path = os.path.abspath("tests/cases/simulator/test1.circ")
    
    # 1. Initialize and load
    if not await emulator.launch_and_initialize(circ_path):
        pytest.fail("Failed to connect or load circuit")
        
    try:
        # 2. Get available circuits and switch
        resp = await emulator.send_command("get_circuits")
        circuits = resp.get("payload", [])
        
        # Look for the target 16-bit adder subcircuit
        target_name = None
        for c in circuits:
            if "16位" in c and "2" in c: # Matching the original test's index 4 logic
                target_name = c
                break
        
        if not target_name:
            # Fallback to the first 16-bit one found
            for c in circuits:
                if "16位" in c:
                    target_name = c
                    break
                    
        if not target_name:
            pytest.fail(f"Could not find 16-bit adder circuit among: {circuits}")
            
        print(f"[Test1] Switching to: {target_name}")
        await emulator.send_command("switch_circuit", name=target_name)
        
        # 3. Test cases
        test_cases = [
            (0x1234, 0x5678, 0, 0x68AC, 0),
            (0xFFFF, 0x0001, 0, 0x0000, 1),
        ]
        
        for a, b, cin, exp_s, exp_cout in test_cases:
            # Set inputs
            await emulator.send_command("set_value", target="X", value=hex(a))
            await emulator.send_command("set_value", target="Y", value=hex(b))
            await emulator.send_command("set_value", target="C0", value=str(cin))
            
            # Wait a tiny bit for async propagation? 
            # Headless API usually propagates immediately after set_value, 
            # but we can do a simple get_value to verify.
            
            s_resp = await emulator.send_command("get_value", target="S")
            cout_resp = await emulator.send_command("get_value", target="C16")
            
            s_val = int(s_resp.get("payload", "0"), 16)
            cout_val = int(cout_resp.get("payload", "0"), 16)
            
            print(f"[Test1] X={hex(a)}, Y={hex(b)} => S={hex(s_val)}, C16={cout_val}")
            
            assert s_val == exp_s
            assert cout_val == exp_cout
            
    finally:
        emulator.close()

@pytest.mark.asyncio
async def test_16bit_fast_adder():
    config = ConfigManager.load_config()
    await run_adder_test(config)

if __name__ == "__main__":
    pytest.main([__file__, "-s"])
