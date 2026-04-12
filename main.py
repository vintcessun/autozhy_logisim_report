import asyncio
import ctypes
from pathlib import Path
from google import genai

from src.utils.config_loader import ConfigManager
from src.agents.content_parsing import ContentParsingAgent
from src.agents.design_agent import DesignAgent
from src.agents.verification_agent import VerificationAgent
from src.agents.report_agent import ReportAgent

import sys

def initialize_system():
    """初始化系统环境"""
    # 注入 vendor 路径，使得 logisim_logic 库全局可用
    vendor_path = str(Path(__file__).parent / "src" / "vendor")
    if vendor_path not in sys.path:
        sys.path.append(vendor_path)
    
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception as e:
        print(f"DPI 意识设置失败: {e}")

async def main():
    # 1. 初始化
    initialize_system()
    config_path = Path("config/config.toml")
    if not config_path.exists():
        print("错误: 找不到 config/config.toml")
        return
    app_config = ConfigManager.load_config(config_path)
    
    # 2. 配置现代 SDK 客户端
    # 自动清洗 base_url 中的版本后缀，以便新 SDK 正确处理
    endpoint = app_config.gemini.base_url.rstrip('/')
    if endpoint.endswith('/v1beta'):
        endpoint = endpoint[:-7]
    elif endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    
    client = genai.Client(
        api_key=app_config.gemini.api_key,
        http_options={'base_url': endpoint}
    )

    # 3. 初始化并注入客户端到智能体
    workspace_dir = Path("workspace")
    input_dir = Path("data_in")
    
    # 全部通过注入 client 和具体型号名来解耦
    parsing_agent = ContentParsingAgent(app_config, workspace_dir, client)
    design_agent = DesignAgent(client, app_config.gemini.model_pro)
    verification_agent = VerificationAgent(app_config, client)
    report_agent = ReportAgent(client, app_config.gemini.model_flash)

    print("--- [1] 启动内容解析 ---")
    tasks = await parsing_agent.run(input_dir)
    print(f"解析到 {len(tasks)} 条任务。")

    executed_tasks = []
    for task in tasks:
        print(f"--- [2] 处理任务: {task.task_name} ({task.task_type}) ---")
        
        # 获取关联电路
        circ_file = workspace_dir / Path(task.source_circ[0]).name if task.source_circ else None
        
        if task.task_type == "design":
            # 运行设计闭环码
            task = await design_agent.run(task, circ_file)
            # 设计完成后，同样需要验证
            if task.status == "finished":
                circ_file = Path(task.source_circ[0])
                task = await verification_agent.run(task, circ_file)
        else:
            # 直接运行验证进程
            if circ_file:
                task = await verification_agent.run(task, circ_file)
        
        executed_tasks.append(task)

    print("--- [3] 生成最终实验报告 ---")
    output_md = Path("output") / "实验报告.md"
    await report_agent.orchestrate(executed_tasks, output_md)
    
    # 清理资源
    verification_agent.close()
    
    print(f"✨ 全流程执行完毕！报告已保存至: {output_md}")

if __name__ == "__main__":
    asyncio.run(main())
