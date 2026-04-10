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

    async def launch_and_initialize(self, circ_path: str = None) -> bool:
        """
        V17.1: 启动仿真器并进行动态视觉就绪检测。
        支持可选的 circ_path 参数，实现启动即加载。
        """
        logisim_exe = Path(self.config.paths.logisim_path).absolute()
        if not logisim_exe.exists():
            print(f"[Emulator] Binary not found: {logisim_exe}")
            return False

        # 构建启动指令 (java -jar 模式直接驱动核心)
        cmd = ["java", "-jar", str(logisim_exe)]
        if circ_path:
            abs_circ = Path(circ_path).absolute()
            if abs_circ.exists():
                cmd.append(str(abs_circ))
                print(f"[Emulator] 启动时将自动加载电路: {abs_circ.name}")
            else:
                print(f"[Emulator] WARNING: 电路文件未找到: {abs_circ}")

        print(f"[Emulator] Physically launching: {' '.join(cmd)}")
        # Capture stderr to debug/startup_error.log
        with open("debug/startup_error.log", "w") as error_log:
            self.process = subprocess.Popen(
                cmd, 
                cwd=str(logisim_exe.parent),
                stderr=error_log
            )
        
        # Poll for main window (Max 60s)
        print("[Emulator] Polling for main window (identifying by 'Logisim' in title)...")
        for attempt in range(60):
            await asyncio.sleep(1)
            all_wins = gw.getAllWindows()
            
            # Primary search: Title contains 'Logisim' and it's a large window
            candidate_wins = [w for w in all_wins if "Logisim" in w.title and w.width > 600 and "autozhy" not in w.title]
            
            if candidate_wins:
                self.main_window = sorted(candidate_wins, key=lambda x: len(x.title))[0]
                print(f"[Emulator] Main window DETECTED: {self.main_window.title} ({self.main_window.width}x{self.main_window.height})")
                
                # Bring to focus
                self.gui.force_focus(self.main_window)
                
                # 视觉握手
                print("[Emulator] Visual Handshake: Waiting for 'Logisim' text or icon...")
                from .ocr_helper import get_ocr_helper
                
                for v_attempt in range(20):
                    await asyncio.sleep(0.5)
                    title_shot = f"debug/handshake_v{v_attempt}.png"
                    pyautogui.screenshot(title_shot, region=(self.main_window.left, self.main_window.top, 500, 60))
                    
                    ocr_res = get_ocr_helper().engine(title_shot)[0]
                    if ocr_res:
                        # V14.1+: 支持关键词或左侧图标触发
                        found_keyword = any(any(k in r[1].lower() for k in ["logisim", "logis", "2.16", "2.2", "main of", "无标题"]) for r in ocr_res)
                        found_icon = any(r[0][0][0] < 30 for r in ocr_res)
                        
                        if found_keyword or found_icon:
                            print(f"[Emulator] VISION READY: Detected in {v_attempt * 0.5}s.")
                            return True
                    else:
                        print(f"[Handshake Scan] Attempt {v_attempt}: No visual signal yet...")
                
                print("[Emulator] Visual Handshake TIMEOUT.")
                return False
        
        print("[Emulator] FAILED to find main window after 60s.")
        return False

    def close(self):
        """
        V17.1: 外科手术级精准关闭 (The God View Close)
        封装清理逻辑，确保零污染、零残留。
        """
        # 1. 尝试通过窗口句柄（hWnd）锁定 PID 进行击杀 (解决 Java 启动导致的一切复杂性)
        if self.main_window:
            try:
                import ctypes
                from ctypes import wintypes
                lpdw_process_id = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(self.main_window._hWnd, ctypes.byref(lpdw_process_id))
                target_pid = lpdw_process_id.value
                if target_pid:
                    print(f"[Emulator] 正在执行窗口溯源清理 (PID: {target_pid})...")
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(target_pid)], capture_output=True)
            except Exception as e:
                print(f"[Emulator] 窗口溯源清理失败: {e}")

        # 2. 兜底：杀掉直接启动的 Popen PID
        if self.process:
            try:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], capture_output=True)
            except: pass
        
        # 3. 清理标记
        self.process = None
        self.main_window = None
        print("[Emulator] Precision cleanup successful.")

    def terminate(self):
        """兼容旧接口，内部映射到 close()"""
        self.close()

    def __del__(self):
        """对象销毁时自动闭环"""
        self.close()
