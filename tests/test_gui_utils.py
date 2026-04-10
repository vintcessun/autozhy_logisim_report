import pytest
from unittest.mock import MagicMock, patch
from src.utils.gui_utils import ScreenControl

def test_get_window_mock():
    """测试窗口获取逻辑 (Mock pygetwindow)"""
    with patch("pygetwindow.getWindowsWithTitle", return_value=[MagicMock(title="Logisim")]):
        gui = ScreenControl()
        win = gui.get_window("Logisim")
        assert win.title == "Logisim"

def test_get_scaling_factor_mock():
    """测试 DPI 缩放获取 (Mock ctypes)"""
    with patch("ctypes.windll.user32.GetDC", return_value=123):
        with patch("ctypes.windll.gdi32.GetDeviceCaps", return_value=144): # 150% 缩放 (144/96 = 1.5)
            with patch("ctypes.windll.user32.ReleaseDC"):
                gui = ScreenControl()
                factor = gui.get_scaling_factor()
                assert factor == 1.5
