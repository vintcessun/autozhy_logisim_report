import asyncio
import ctypes
import shutil
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
    
    # 清理 output 目录
    output_dir = Path("output")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 2. 配置现代 SDK 客户端
    endpoint = app_config.gemini.base_url.rstrip('/')
    if endpoint.endswith('/v1beta'):
        endpoint = endpoint[:-7]
    elif endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    
    client = genai.Client(
        api_key=app_config.gemini.api_key,
        http_options={'base_url': endpoint}
    )

    # 3. 初始化智能体
    workspace_dir = Path("workspace")
    input_dir = Path("data_in")
    
    parsing_agent = ContentParsingAgent(app_config, workspace_dir, client)
    design_agent = DesignAgent(client, app_config, app_config.gemini.model_flash)
    verification_agent = VerificationAgent(app_config, client)
    report_agent = ReportAgent(client, app_config.gemini.model_pro, app_config.gemini.model_flash)

    # --- [1] 内容解析 ---
    print("--- [1] 启动内容解析 ---")
    parsing_result = await parsing_agent.run(input_dir)
    print(f"解析到 {len(parsing_result.verification_tasks)} 条验证任务，{len(parsing_result.design_tasks)} 条设计任务。")

    # --- [2] 验证性实验 ---
    completed_verification = []
    print("\n--- [2] 处理验证性实验 ---")
    for task in parsing_result.verification_tasks:
        circ_file = workspace_dir / Path(task.source_circ[0]).name if task.source_circ else None
        if circ_file and circ_file.exists():
            task = await verification_agent.run(task, circ_file)
        completed_verification.append(task)

    # --- [3] 设计性实验 ---
    completed_design = []
    all_design_subs = []
    print("\n--- [3] 处理设计性实验 ---")
    for task in parsing_result.design_tasks:
        source_circ = workspace_dir / Path(task.source_circ[0]).name if task.source_circ else None
        ref_circ = workspace_dir / Path(task.reference_circ).name if task.reference_circ else None
        
        # 3a. DesignAgent: 截图 + 拷贝 + 拆解
        task, sub_tasks = await design_agent.run(task, source_circ, ref_circ)
        completed_design.append(task)
        
        # 3b. 验证子任务
        for sub in sub_tasks:
            # 子任务继承了命好名后的电路路径
            sub_circ = Path(sub.source_circ[0])
            if sub_circ.exists():
                sub = await verification_agent.run(sub, sub_circ)
            all_design_subs.append(sub)

    # --- [4] 生成最终实验报告 ---
    print("\n--- [4] 生成最终实验报告 ---")
    output_md = output_dir / "实验报告.md"
    await report_agent.generate(
        verification_tasks=completed_verification,
        design_tasks=completed_design,
        design_sub_tasks=all_design_subs,
        instruction_docs=parsing_result.instruction_docs,
        reference_reports=parsing_result.reference_reports,
        output_path=output_md
    )
    
    # 清理资源
    verification_agent.close()
    
    print(f"\n✨ 全流程执行完毕！")
    print(f"   报告保存至: {output_md}")
    print(f"   电路归档至: {output_dir}/提交电路/")
    print(f"   资源保存至: {output_dir}/实验报告.assets/")

if __name__ == "__main__":
    asyncio.run(main())
