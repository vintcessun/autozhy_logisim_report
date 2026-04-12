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

async def run_multiplier_test(config):
    emulator = LogisimEmulator(config, None)
    circ_path = os.path.abspath("tests/cases/simulator/test2.circ")
    
    if not await emulator.launch_and_initialize(circ_path):
        pytest.fail("Failed to connect or load circuit")
        
    try:
        # 1. 寻找包含测试台的子电路 (Looking for the one with 'END' or 'CLK')
        resp = await emulator.send_command("get_circuits")
        circuits = resp.get("payload", [])
        
        target_name = None
        # 优先寻找包含多个组件标签的电路（补码/改进版）
        for c in circuits:
            if "补码" in c and ("改进" in c or "复杂" in c):
                target_name = c
                break
        
        if not target_name:
            target_name = circuits[0] if circuits else "main"
            
        print(f"[Test2] Switching to: {target_name}")
        await emulator.send_command("switch_circuit", name=target_name)
        
        # 2. 尝试发现内部组件对应的标签 (API-based Discovery)
        # 探测预期标签是否存在
        clk_candidates = ["CLK", "时钟", "Clock"]
        reset_candidates = ["λ", "复位", "Reset"]
        end_candidates = ["END", "结束", "Done"]
        product_candidates = ["Product", "乘积", "Result"]
        
        async def resolve_label(candidates):
            for c in candidates:
                r = await emulator.send_command("get_value", target=c)
                if r.get("status") == "ok":
                    payload = str(r.get("payload", "unknown")).lower()
                    if "unknown" not in payload and "empty" not in payload:
                        return c
            return candidates[0] # Fallback

        clk_label = await resolve_label(clk_candidates)
        reset_label = await resolve_label(reset_candidates)
        end_label = await resolve_label(end_candidates)
        product_label = await resolve_label(product_candidates)
        
        print(f"[Test2] Resolved Labels -> CLK: {clk_label}, RST: {reset_label}, END: {end_label}, OUT: {product_label}")
        
        test_data = [
            (3, 5, 15),
            (12, 10, 120),
        ]
        
        for a, b, expected in test_data:
            print(f"\n[Test2] Testing {a} * {b}...")
            
            # Reset
            await emulator.send_command("set_value", target=reset_label, value="1")
            await emulator.send_command("set_value", target=reset_label, value="0")
            
            # Set Inputs (Based on discovery, usually X and Y)
            await emulator.send_command("set_value", target="X", value=str(a))
            await emulator.send_command("set_value", target="Y", value=str(b))
            
            # Tick Until DONE
            # 尝试使用 0x1 并增加超时
            tick_resp = await emulator.send_command("tick_until", 
                                                    target="END", 
                                                    expected="0x1", 
                                                    clock="时钟", 
                                                    max=3000)
            
            if tick_resp.get("status") != "ok":
                print(f"[Warning] Tick Until failure: {tick_resp.get('message')}")
            else:
                print(f"[Test2] Completed in {tick_resp.get('ticks')} ticks.")
                
            # Verify result
            actual_resp = await emulator.send_command("get_value", target=product_label)
            actual_hex = actual_resp.get("payload", "0x0")
            actual_val = int(actual_hex, 16) if "unknown" not in actual_hex else 0
            
            print(f"[Test2] Result: {actual_val} (Expected {expected})")
            assert actual_val == expected
            
    finally:
        emulator.close()

@pytest.mark.asyncio
async def test_8bit_multiplier_agent():
    config = ConfigManager.load_config()
    await run_multiplier_test(config)

if __name__ == "__main__":
    pytest.main([__file__, "-s"])
