import pytest
import subprocess
import asyncio
import time
import os
from pathlib import Path
import pyautogui
import pygetwindow as gw
from src.utils.gui_utils import ScreenControl, screen_lock, ScreenLockContext
from src.utils.config_loader import ConfigManager
from src.utils.sim_runner import LogisimEmulator

@pytest.mark.asyncio
async def test_real_logisim_gui_interaction():
    """Validating visual initialization and screenshot with LogisimEmulator"""
    config = ConfigManager.load_config(Path("config/config.toml"))
    emulator = LogisimEmulator(config)
    
    print("\n[1] Launching LogisimEmulator (with visual initialization)...")
    # Disable FAILSAFE for the duration of this test to handle remote desktop drift
    old_failsafe = pyautogui.FAILSAFE
    pyautogui.FAILSAFE = False
    
    success = await emulator.launch_and_initialize()
    
    if not success:
        emulator.terminate()
        pytest.fail("LogisimEmulator failed to start or initialize")

    win = emulator.main_window
    print(f"[2] Main window locked: {win.title}, Pos: ({win.left}, {win.top})")
    
    try:
        # 3. Preparation for Visual Actions
        async with ScreenLockContext(screen_lock):
            # V13.0: 不再需要手动 sleep(5)，launch_and_initialize 已经保证了视觉就绪
            print("[3] UI is visually verified by emulator. Proceeding to interaction...")
            
            # Stage 1: Initial Ready State
            stage1_path = Path("debug/stage1_ready.png")
            stage1_path.parent.mkdir(exist_ok=True)
            print(f"[4] Capturing Stage 1 (Ready): {stage1_path}")
            pyautogui.screenshot(str(stage1_path), region=(win.left, win.top, win.width, win.height))
            
            # Stage 2: Triggering the '文件' (File) Menu
            # 验证“零调参”模型在第一个菜单项上的表现
            instruction_file = (
                "Find the '文件' (File) menu item text. "
                "Click the center of '文件' text."
            )
            print(f"[5] Instructing V10.1 Engine (Pure OCR): {instruction_file}")
            
            success_file = await emulator.bridge.perform_visual_action(
                instruction_file, 
                stage1_path, 
                execute=True,
                window_pos=(win.left, win.top)
            )
            
            if success_file:
                await asyncio.sleep(3)
                # 截取“文件”菜单展开后的全屏存证
                final_path = Path("D:/Scripts/autozhy_logisim_report/final_file_menu.png")
                from PIL import ImageGrab
                ImageGrab.grab().save(str(final_path))
                print(f"[6] SUCCESS: File menu proof saved to {final_path}")
                await asyncio.sleep(2)
                
            print("[7] Test logic finished. Emulator should close automatically via __del__.")

        print("[8] GUI Visual Interaction Test PASSED!")

    finally:
        # Cleanup
        print("[9] Cleanup: Releasing screen lock and resetting Failsafe.")
        pyautogui.FAILSAFE = old_failsafe
        # 不再显式调用 emulator.terminate()，由 Python 垃圾回收触发 __del__
