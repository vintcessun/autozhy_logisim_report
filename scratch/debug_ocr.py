import cv2
from pathlib import Path
from src.utils.ocr_helper import get_ocr_helper

def debug_ocr_location():
    img_path = Path("debug/stage1_ready.png")
    if not img_path.exists():
        print("未找到 stage1_ready.png，请先运行测试脚本。")
        return

    # 1. 再次识别
    coord = get_ocr_helper().find_text_coordinates(img_path, "文件")
    
    if coord:
        print(f"OCR 定位坐标: {coord}")
        # 2. 绘制红点
        img = cv2.imread(str(img_path))
        cv2.circle(img, coord, 15, (0, 0, 255), -1) # 画一个大红点
        
        # 3. 保存诊断图
        debug_path = Path("debug/ocr_debug_mapping.png")
        cv2.imwrite(str(debug_path), img)
        print(f"诊断图已保存至: {debug_path}")
    else:
        print("OCR 未能识别出 '文件'")

if __name__ == "__main__":
    debug_ocr_location()
