import asyncio
import ctypes
from pathlib import Path
import google.generativeai as genai

from src.utils.config_loader import ConfigManager
from src.agents.content_parsing import ContentParsingAgent
from src.agents.design_agent import DesignAgent
from src.agents.verification_agent import VerificationAgent
from src.agents.report_agent import ReportAgent

def initialize_system():
    """初始化系统环境"""
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
    
    # 2. 配置模型
    genai.configure(api_key=app_config.gemini.api_key, transport="rest")
    model_pro = genai.GenerativeModel(app_config.gemini.model_pro)
    model_flash = genai.GenerativeModel(app_config.gemini.model_flash)

    # 3. 初始化所有智能体
    workspace_dir = Path("workspace")
    input_dir = Path("data_in")
    
    parsing_agent = ContentParsingAgent(app_config, workspace_dir)
    parsing_agent.extractor.model = model_flash
    
    design_agent = DesignAgent(model_pro)
    verification_agent = VerificationAgent(app_config)
    report_agent = ReportAgent(model_flash)

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
