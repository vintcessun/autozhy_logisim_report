import sys
from pathlib import Path
import time

# 强制加入路径
sys.path.append('.')
from src.utils.ocr_helper import get_ocr_helper

def benchmark_ocr():
    img_path = Path("debug/stage1_ready.png")
    if not img_path.exists():
        print("FileNotFound: stage1_ready.png")
        return

    helper = get_ocr_helper()
    print("\n=== 显卡加速性能连发测试 ===")
    
    for i in range(1, 6):
        start = time.time()
        coord = helper.find_text_coordinates(img_path, "文件")
        elapsed = (time.time() - start) * 1000
        print(f"第 {i} 次扫描耗时: {elapsed:.2f}ms | 坐标: {coord}")

if __name__ == "__main__":
    benchmark_ocr()
