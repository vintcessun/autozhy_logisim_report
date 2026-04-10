import cv2
import numpy as np
from pathlib import Path

def draw_grid():
    img_path = Path("debug/stage1_ready.png")
    if not img_path.exists():
        print("未找到截图")
        return

    img = cv2.imread(str(img_path))
    h, w, _ = img.shape

    # 绘制垂直线 (X轴)
    for x in range(0, w, 20):
        color = (0, 255, 0) if x % 100 == 0 else (200, 200, 200)
        cv2.line(img, (x, 0), (x, h), color, 1)
        if x % 40 == 0:
            cv2.putText(img, str(x), (x, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

    # 绘制水平线 (Y轴)
    for y in range(0, h, 20):
        color = (0, 255, 0) if y % 100 == 0 else (200, 200, 200)
        cv2.line(img, (0, y), (w, y), color, 1)
        if y % 40 == 0:
            cv2.putText(img, str(y), (5, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)

    # 标记我们之前的点击点 (56, 41)
    cv2.circle(img, (56, 41), 5, (255, 0, 0), -1)

    debug_path = Path("debug/pixel_grid_debug.png")
    cv2.imwrite(str(debug_path), img)
    print(f"网格图已保存: {debug_path}")

if __name__ == "__main__":
    draw_grid()
