import asyncio
import ctypes
import pyautogui
import pygetwindow as gw
from typing import Optional

# 符合 ADR-0002: 设置 DPI 感知，防止坐标偏移
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# 全局屏幕互斥锁：符合 SPECIFICATIONS.md Section 3
screen_lock = asyncio.Lock()

class ScreenControl:
    """GUI 操控核心类，PascalCase 命名类"""

    def __init__(self):
        # Fail-Safe 机制交由全局或测试脚本显式控制，不在构造函数中硬编码
        pass

    @staticmethod
    def get_window(title_keyword: str) -> Optional[gw.Window]:
        """根据关键字获取窗口对象"""
        wins = gw.getWindowsWithTitle(title_keyword)
        return wins[0] if wins else None

    @staticmethod
    def force_focus(window: gw.Window):
        """强制置顶并修正焦点"""
        if not window:
            return
        
        try:
            if window.isMinimized:
                window.restore()
            window.activate()
        except Exception as e:
            # 在某些 Windows 环境下可能会超时报错，但通常窗口已处于可见状态
            print(f"[GUI] 激活窗口时发生非致命异常: {e}")
            
        # 针对 Windows 的 Alt 键破解焦点锁定逻辑 (ADR-0002)
        pyautogui.press('alt')

    def get_scaling_factor(self) -> float:
        """获取当前系统的 DPI 缩放系数"""
        try:
            # 逻辑：逻辑：使用 Windows 系统调用获取主显示器的真实像素缩放
            user32 = ctypes.windll.user32
            h_dc_screen = user32.GetDC(0)
            log_pixels_y = ctypes.windll.gdi32.GetDeviceCaps(h_dc_screen, 90)  # LOGPIXELSY
            user32.ReleaseDC(0, h_dc_screen)
            return log_pixels_y / 96.0  # 96 是标准的 100% 缩放
        except Exception:
            return 1.0

class ScreenLockContext:
    """锁的上下文管理器，确保异常时也能释放锁"""
    def __init__(self, lock: asyncio.Lock):
        self.lock = lock

    async def __aenter__(self):
        await self.lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.lock.release()
