import sys
import asyncio
from pathlib import Path
from google import genai

# 统一注入项目根目录和 src 目录
project_root = Path(__file__).parents[1]
sys.path.append(str(project_root))

from src.utils.config_loader import ConfigManager
from src.agents.design_agent import DesignAgent
from src.core.models import TaskRecord

async def run_smoketest():
    """
    冒烟测试：设计一个极其简单的 AND 门。
    使用的是重构后的 DesignAgent，它会加载 prompts/ 目录下的提示词。
    """
    config_path = project_root / "config" / "config.toml"
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
    # 传入 Flash 模型，使用 2.0-flash 以保证速度
    agent = DesignAgent(client, app_config.gemini.model_pro, app_config.gemini.model_flash)
    
    task = TaskRecord(
        task_id="smoketest_001",
        task_name="极简AND门设计",
        task_type="design",
        analysis_raw="目标：设计一个 AND 门，将输入 A 和 B 连接到一个输出 OUT。"
    )
    
    print("\n--- [SMOKETEST] Starting Minimal Design Task ---")
    
    # 运行设计 (从空图开始)
    updated_task = await agent.run(task, None) 
    
    if updated_task.status == "finished":
        result_file = Path(updated_task.source_circ[0])
        print(f"Smoketest Success! File: {result_file}")
    else:
        print(f"Smoketest Failed: {updated_task.analysis_raw}")

if __name__ == "__main__":
    asyncio.run(run_smoketest())
