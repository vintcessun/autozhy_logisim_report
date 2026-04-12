import sys
import subprocess
from pathlib import Path

# 立即注入 vendor 路径
vendor_path = str(Path.cwd() / "src" / "vendor")
if vendor_path not in sys.path:
    sys.path.append(vendor_path)

import asyncio
from src.utils.config_loader import ConfigManager
from src.agents.design_agent import DesignAgent
from src.core.models import TaskRecord
from google import genai

async def run_smoketest():
    """
    冒烟测试：设计一个极其简单的 AND 门。
    目的是验证：API 请求、脚本执行、UI 自动开启这三步是否畅通。
    """
    config_path = Path("config/config.toml")
    app_config = ConfigManager.load_config(config_path)
    
    endpoint = app_config.gemini.base_url.rstrip('/')
    if endpoint.endswith('/v1beta'):
        endpoint = endpoint[:-7]
    elif endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    
    client = genai.Client(
        api_key=app_config.gemini.api_key,
        http_options={'base_url': endpoint}
    )
    agent = DesignAgent(client, app_config.gemini.model_pro)
    
    task = TaskRecord(
        task_id="smoketest_001",
        task_name="极简AND门设计",
        task_type="design",
        analysis_raw="目标：设计一个 AND 门，将输入 A 和 B 连接到一个输出 OUT。"
    )
    
    print("\n--- [SMOKETEST] Starting Minimal Design Task ---")
    
    # 运行设计
    updated_task = await agent.run(task, None) # 传入 None 表示从空图开始
    
    if updated_task.status == "finished":
        result_file = Path(updated_task.source_circ[0])
        print(f"Smoketest Success! File: {result_file}")
    else:
        print(f"Smoketest Failed: {updated_task.analysis_raw}")

if __name__ == "__main__":
    asyncio.run(run_smoketest())
