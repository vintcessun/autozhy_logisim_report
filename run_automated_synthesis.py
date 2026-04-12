import asyncio
import os
import sys
from pathlib import Path
from google.genai import Client, types

# Fix sys.path
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))
vendor_path = project_root / "src" / "vendor"
if str(vendor_path) not in sys.path:
    sys.path.append(str(vendor_path))

from src.utils.config_loader import ConfigManager
from src.agents.strategy_agent import StrategyAgent
from src.agents.execution_agent import ExecutionAgent
from tests.test_final_grading import run_grading

async def main():
    print("===== [ORCHESTRATOR] Starting Automated CLA Synthesis Pipeline =====")
    
    # 1. Setup Models & Config
    app_config = ConfigManager.load_config(project_root / "config" / "config.toml")
    
    # Clean base_url as in main.py
    endpoint = app_config.gemini.base_url.rstrip('/')
    if endpoint.endswith('/v1beta'):
        endpoint = endpoint[:-7]
    elif endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    
    client = Client(
        api_key=app_config.gemini.api_key,
        http_options={'base_url': endpoint}
    )

    pro_model_id = app_config.gemini.model_pro
    flash_model_id = app_config.gemini.model_flash

    print(f"--- [INIT] Using Pro Model: {pro_model_id}")
    print(f"--- [INIT] Using Flash Model: {flash_model_id}")

    strategy_agent = StrategyAgent(client, pro_model_id)
    execution_agent = ExecutionAgent(client, flash_model_id)

    # 2. Paths
    info_path = project_root / "tests" / "cases" / "design" / "info.txt"
    target_path = project_root / "tests" / "cases" / "design" / "16位快速加法器设计_design.circ"
    
    # 3. Outer Loop (Overall attempt at the task)
    max_rounds = 3
    for round_idx in range(max_rounds):
        print(f"\n>>> [GLOBAL ROUND {round_idx + 1}] <<<")
        
        # A. Strategy Phase (PRO)
        # Pro reads info.txt and current target.circ analysis
        analysis = "待生成"
        if target_path.exists():
             from logisim_logic import load_project
             try:
                 analysis = f"当前电路含有 {len(load_project(str(target_path)).circuits[0].components)} 个组件"
             except:
                 analysis = "电路解析异常"

        print("--- [PRO] Formulating strategy...")
        design_spec = await strategy_agent.generate_design_spec(info_path, analysis)
        print(f"--- [PRO] Design Spec Received (Len: {len(design_spec)})")

        # B. Execution & Internal Verification Phase (FLASH)
        # Flash iterates internally until success or cap
        print("--- [FLASH] Starting execution loop...")
        final_script = await execution_agent.generate_and_verify_circuit(design_spec, target_path)
        
        if final_script == "FAILED_TO_SELF_VERIFY":
            print("--- [WARNING] Flash failed to internally verify design. Re-consulting Pro...")
            continue
            
        print("--- [SUCCESS] Flash claims design is ready for grading.")
        
        # C. Final Black-Box Grading (EXTERNAL TEST)
        # This script uses the forbidden expected_result.circ
        success = run_grading(str(target_path))
        
        if success:
            print("\n" + "="*50)
            print("MISSION ACCOMPLISHED! THE DESIGN PASSED THE BLACK-BOX GRADING.")
            print("="*50)
            break
        else:
            print("\n--- [FAIL] Final Grading Failed. Restarting from Strategy Phase...")
    
    else:
        print("\n" + "!"*50)
        print("MISSION FAILED: Maximum global rounds reached.")
        print("!"*50)

if __name__ == "__main__":
    asyncio.run(main())
