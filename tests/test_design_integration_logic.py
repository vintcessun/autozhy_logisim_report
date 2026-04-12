import sys
import random
import asyncio
from pathlib import Path

# Setup paths - 统一使用项目根目录
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
from src.utils.config_loader import ConfigManager
from src.agents.design_agent import DesignAgent
from src.core.models import TaskRecord
from google import genai
from logisim_logic import load_project, extract_logical_circuit
from src.utils.logic_simulator import LogicSimulator

def run_grading_check(target_path: Path, ref_path: Path) -> bool:
    """
    10+4 矩阵校验：10组随机 + 4组关键边界 (0+X, X+0, MAX+X, X+MAX)
    该校验为黑盒测试，不提供具体真值反馈给模型。
    """
    print(f"\n[GRADING] Starting Final Alignment Check vs Reference: {ref_path.name}")
    
    try:
        # Load DUT (Generated circuit)
        proj_dut = load_project(str(target_path))
        circ_dut = proj_dut.main_circuit or proj_dut.circuits[0]
        sim_dut = LogicSimulator(extract_logical_circuit(circ_dut, project=proj_dut))
        
        # Load Oracle (Reference circuit)
        proj_ref = load_project(str(ref_path))
        # 识别参考电路中的主逻辑电路
        circ_ref = next((c for c in proj_ref.circuits if "16" in c.name), proj_ref.circuits[0])
        sim_ref = LogicSimulator(extract_logical_circuit(circ_ref, project=proj_ref))
        
        # 构造 10+4 测试矩阵
        test_cases = []
        test_cases.append((0, random.randint(0, 0xFFFF), random.randint(0, 1))) # 0 + X
        test_cases.append((random.randint(0, 0xFFFF), 0, random.randint(0, 1))) # X + 0
        test_cases.append((0xFFFF, random.randint(0, 0xFFFF), random.randint(0, 1))) # MAX + X
        test_cases.append((random.randint(0, 0xFFFF), 0xFFFF, random.randint(0, 1))) # X + MAX
        for _ in range(10):
            test_cases.append((random.randint(0, 0xFFFF), random.randint(0, 0xFFFF), random.randint(0, 1)))
            
        fail_count = 0
        for i, (a, b, cin) in enumerate(test_cases):
            out_dut = sim_dut.simulate({"A": a, "B": b, "Cin": cin})
            out_ref = sim_ref.simulate({"X": a, "Y": b, "C0": cin})
            
            s_dut, c_dut = out_dut.get("S"), out_dut.get("Cout")
            s_ref, c_ref = out_ref.get("S"), out_ref.get("C16")
            
            if s_dut != s_ref or c_dut != c_ref:
                print(f"[FAIL] Case {i+1}: Input A={a:04X}, B={b:04X}, Cin={cin} -> Misaligned!")
                fail_count += 1
                
        if fail_count == 0:
            print("\n[PASS] 100% Alignment with Reference!")
            return True
        else:
            print(f"\n[FAIL] Found {fail_count} misalignments. Testing Failed.")
            return False
            
    except Exception as e:
        print(f"[RECOVERY ERROR] Grading could not complete: {e}")
        return False

@pytest.mark.asyncio
async def test_logic_design_integration():
    """
    核心集成测试：驱动 DesignAgent 自主设计，并进行黑盒对撞校验。
    """
    # 初始化路径
    info_path = project_root / "tests/cases/design/info.txt"
    template_path = project_root / "tests/cases/design/target.circ"
    ref_path = project_root / "tests/cases/design/expected_result.circ"
    config_path = project_root / "config/config.toml"
    
    app_config = ConfigManager.load_config(config_path)
    
    # 初始化 LLM 客户端
    endpoint = app_config.gemini.base_url.rstrip('/')
    if endpoint.endswith('/v1beta'): endpoint = endpoint[:-7]
    elif endpoint.endswith('/v1'): endpoint = endpoint[:-3]
    
    client = genai.Client(api_key=app_config.gemini.api_key, http_options={'base_url': endpoint})
    
    # 使用 Pro 模型进行高阶架构设计
    agent = DesignAgent(client, app_config.gemini.model_pro, app_config.gemini.model_flash)
    
    design_goal = info_path.read_text(encoding="utf-8").strip()
    task = TaskRecord(
        task_id="integration_001",
        task_name="16位快速加法器设计",
        task_type="design",
        analysis_raw=f"目标：{design_goal}"
    )
    
    print("\n--- Starting Design Loop ---")
    updated_task = await agent.run(task, template_path)
    
    if updated_task.status != "finished":
        pytest.fail(f"Agent internally failed: {updated_task.analysis_raw}")
    
    result_file = Path(updated_task.source_circ[0])
    aligned = run_grading_check(result_file, ref_path)
    assert aligned, "The designed circuit is NOT functionally equivalent to the oracle."

if __name__ == "__main__":
    asyncio.run(test_logic_design_integration())
