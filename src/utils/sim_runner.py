import asyncio
import subprocess
import time
import pyautogui
import pygetwindow as gw
from pathlib import Path
from .tars_bridge import TarsBridge
from .gui_utils import ScreenControl, screen_lock, ScreenLockContext

class LogisimEmulator:
    """Logisim 仿真器运行管理器，封装启动、初始化及视觉操作"""

    def __init__(self, config):
        self.config = config
        self.bridge = TarsBridge(config.ollama)
        self.gui = ScreenControl()
        self.process = None
        self.main_window = None

    async def launch_and_initialize(self) -> bool:
        """Launch emulator and poll for the main window automatically"""
        logisim_exe = Path(self.config.paths.logisim_path).absolute()
        if not logisim_exe.exists():
            print(f"[Emulator] Binary not found: {logisim_exe}")
            return False

        print(f"[Emulator] Physically launching: {logisim_exe} in CWD: {logisim_exe.parent}")
        # Capture stderr to debug/startup_error.log
        with open("debug/startup_error.log", "w") as error_log:
            self.process = subprocess.Popen(
                [str(logisim_exe)], 
                cwd=str(logisim_exe.parent),
                stderr=error_log
            )
        
        # Poll for main window (Max 60s)
        print("[Emulator] Polling for main window (identifying by 'Logisim' in title)...")
        for attempt in range(60):
            await asyncio.sleep(1)
            all_wins = gw.getAllWindows()
            
            # Primary search: Title contains 'Logisim' and it's a large window
            # Usually the top-left text has 'Logisim' 
            candidate_wins = [w for w in all_wins if "Logisim" in w.title and w.width > 600 and "autozhy" not in w.title]
            
            if candidate_wins:
                self.main_window = sorted(candidate_wins, key=lambda x: len(x.title))[0]
                print(f"[Emulator] Main window DETECTED: {self.main_window.title} ({self.main_window.width}x{self.main_window.height})")
                
                # Bring to focus so we can use it
                self.gui.force_focus(self.main_window)
                
                # V13.0: 视觉握手 - 动态就绪检测
                print("[Emulator] Visual Handshake: Waiting for 'Logisim' text in title area...")
                from .ocr_helper import get_ocr_helper
                
                # 提高扫描频率 (0.5s 步进) 以实现毫秒级响应
                for v_attempt in range(20):
                    await asyncio.sleep(0.5)
                    # 截取标题栏关键区域 (稍微扩宽以适配不同 DPI)
                    title_shot = f"debug/handshake_v{v_attempt}.png"
                    pyautogui.screenshot(title_shot, region=(self.main_window.left, self.main_window.top, 400, 50))
                    
                    ocr_res = get_ocr_helper().engine(title_shot)[0]
                    if ocr_res and any("Logisim" in r[1] for r in ocr_res):
                        print(f"[Emulator] VISION READY: Detected in {v_attempt * 0.5}s.")
                        return True
                
                print("[Emulator] Visual Handshake TIMEOUT: Window visible but UI content not verified.")
                return False
        
        print("[Emulator] FAILED to find main window after 60s.")
        return False

    def terminate(self):
        """强制关闭仿真器及所有关联子进程 (V8.0 加固)"""
        if self.process:
            try:
                # Windows 平台：使用 taskkill 强制切断进程树
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], capture_output=True)
                print(f"[Emulator] 进程树 {self.process.pid} 已强制终止。")
            except Exception as e:
                print(f"[Emulator] 终止进程时出错: {e}")
                self.process.terminate()
            self.process = None # 标记为已清理

    def __del__(self):
        """对象销毁时自动调用，确保仿真器关闭"""
        # 注意：此处使用简单的 terminate 调用
        self.terminate()
