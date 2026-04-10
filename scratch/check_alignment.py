import pyautogui
import ctypes
from PIL import ImageGrab

def check_alignment():
    print("=== DPI 坐标对齐普查 ===")
    
    # 1. 获取 PyAutoGUI 看到的逻辑分辨率
    logical_w, logical_h = pyautogui.size()
    print(f"逻辑分辨率 (PyAutoGUI): {logical_w} x {logical_h}")
    
    # 2. 获取实际的全屏快照分辨率 (物理像素)
    # ImageGrab 在 Windows 下通常能抓到真实的物理像素
    img = ImageGrab.grab()
    physical_w, physical_h = img.size
    print(f"物理分辨率 (Screenshot): {physical_w} x {physical_h}")
    
    # 3. 计算缩放比 (Scaling Factor)
    scaling_x = physical_w / logical_w
    scaling_y = physical_h / logical_h
    print(f"检测到 DPI 缩放因子: {scaling_x:.2f}x (X), {scaling_y:.2f}x (Y)")
    
    # 4. 检查 DPI 感知的底层 API
    try:
        shcore = ctypes.windll.shcore
        awareness = ctypes.c_int()
        shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
        print(f"当前进程 DPI 感知级别: {awareness.value}")
    except:
        print("无法获取底层 DPI 感知状态")

if __name__ == "__main__":
    check_alignment()
