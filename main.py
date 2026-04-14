import asyncio
import ctypes
import shutil
from pathlib import Path
from google import genai

from src.utils.config_loader import ConfigManager
from src.utils.cache_manager import CacheManager
from src.agents.content_parsing import ContentParsingAgent
from src.agents.design_agent import DesignAgent
from src.agents.verification_agent import VerificationAgent
from src.agents.report_agent import ReportAgent

import sys
import argparse

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

def resolve_circuit_path(initial_path: str | Path | None, workspace_dir: Path) -> Path | None:
    """
    强化的电路路径解析方案：
    1. 强制转为绝对路径校验
    2. 如果失败，尝试拼接 workspace_dir 校验
    3. 如果失败，在项目根目录下递归兜底搜索文件名
    """
    if not initial_path:
        return None
    
    # 确保是 Path 对象
    p = Path(initial_path)
    filename = p.name
    
    # 策略 1: 直接作为绝对路径（或当前工作目录相对路径）
    abs_p = p.absolute()
    if abs_p.exists() and abs_p.is_file():
        return abs_p
    
    # 策略 2: 尝试在 workspace 目录下寻找
    ws_p = (workspace_dir / filename).absolute()
    if ws_p.exists() and ws_p.is_file():
        return ws_p
        
    # 策略 3: 全局递归搜索文件名
    print(f"[PathResolver] 警告: 路径失效 {initial_path}，尝试在项目目录中搜索 {filename}...")
    for found_p in Path(".").rglob(filename):
        if found_p.is_file():
            resolved = found_p.absolute()
            print(f"[PathResolver] 兜底搜索成功: {resolved}")
            return resolved
            
    print(f"[PathResolver] 错误: 无法定位电路文件 {filename}")
    return None

async def main():
    # 1. 解析参数
    parser = argparse.ArgumentParser(description="AutoZHY Logisim Report Generator")
    parser.add_argument("--clear-cache", action="store_true", help="清除缓存并重新开始")
    args = parser.parse_args()

    initialize_system()
    config_path = Path("config/config.toml")
    if not config_path.exists():
        print("错误: 找不到 config/config.toml")
        return
    app_config = ConfigManager.load_config(config_path)
    
    # 1.1 初始化缓存管理器
    cache = CacheManager()
    if args.clear_cache:
        cache.clear()
    else:
        cache.initialize()

    # 清理 output 目录 (仅在清楚缓存或目录不存在时创建)
    output_dir = Path("output")
    if args.clear_cache and output_dir.exists():
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
    
    parsing_agent = ContentParsingAgent(app_config, workspace_dir, client, cache=cache)
    design_agent = DesignAgent(client, app_config, app_config.gemini.model_flash, cache=cache)
    verification_agent = VerificationAgent(app_config, client, cache=cache)
    report_agent = ReportAgent(client, app_config.gemini.model_pro, app_config.gemini.model_flash)

    # --- [1] 内容解析 ---
    print("--- [1] 启动内容解析 ---")
    parsing_result = await parsing_agent.run(input_dir)
    print(f"解析到 {len(parsing_result.verification_tasks)} 条验证任务，{len(parsing_result.design_tasks)} 条设计任务。")

    # --- [2] 验证性实验 ---
    completed_verification = []
    print("\n--- [2] 处理验证性实验 ---")
    for task in parsing_result.verification_tasks:
        circ_path = task.source_circ[0] if task.source_circ else None
        resolved_circ = resolve_circuit_path(circ_path, workspace_dir)
        
        if resolved_circ:
            task = await verification_agent.run(task, resolved_circ)
        else:
            print(f"[Main] 跳过任务 {task.task_name}，找不到电路文件。")
            
        completed_verification.append(task)

    # --- [3] 设计性实验 ---
    completed_design = []
    all_design_subs = []
    print("\n--- [3] 处理设计性实验 ---")
    for task in parsing_result.design_tasks:
        source_path = task.source_circ[0] if task.source_circ else None
        ref_path = task.reference_circ
        
        resolved_source = resolve_circuit_path(source_path, workspace_dir)
        resolved_ref = resolve_circuit_path(ref_path, workspace_dir)
        
        # 3a. DesignAgent: 截图 + 拷贝 + 拆解
        task, sub_tasks = await design_agent.run(task, resolved_source, resolved_ref)
        completed_design.append(task)
        
        # 3b. 验证子任务
        for sub in sub_tasks:
            # 子任务继承了命好名后的电路路径，通常已经在 DesignAgent 里处理过
            # 但为了保险，仍然进行一次解析（特别是因为它会强制转为绝对路径）
            sub_circ_path = sub.source_circ[0] if sub.source_circ else None
            resolved_sub = resolve_circuit_path(sub_circ_path, workspace_dir)
            
            if resolved_sub:
                sub = await verification_agent.run(sub, resolved_sub)
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
    
    print(f"\n全流程执行完毕！")
    print(f"   报告保存至: {output_md}")
    print(f"   电路归档至: {output_dir}/提交电路/")
    print(f"   资源保存至: {output_dir}/实验报告.assets/")

if __name__ == "__main__":
    asyncio.run(main())
