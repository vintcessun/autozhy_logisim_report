import asyncio
import sys
import os
sys.path.append(os.getcwd())
from pathlib import Path
from src.utils.tars_bridge import TarsBridge
from src.utils.config_loader import ConfigManager

async def diagnostic():
    print("--- UI-TARS 视觉定位离线诊断 ---")
    config = ConfigManager.load_config(Path("config/config.toml"))
    bridge = TarsBridge(config.ollama)
    
    # 指向之前失败时的现场截图
    screenshot_path = Path("debug/startup_dump.png")
    if not screenshot_path.exists():
        print(f"错误: 找不到诊断图片 {screenshot_path}")
        return

    instruction = "Find the 'No' or '否' button in the update dialog and click it."
    print(f"正在发送指令: {instruction}")
    
    # 我们修改 perform_visual_action 内部来打印原始输出
    # 这里直接调用以观察效果
    success = await bridge.perform_visual_action(instruction, screenshot_path)
    
    if success:
        print("\n[结果] 诊断通过：视觉链路成功解析并产生了点击动作。")
    else:
        print("\n[结果] 诊断失败：模型未返回正确格式或解析器无法处理输出。")

if __name__ == "__main__":
    asyncio.run(diagnostic())
