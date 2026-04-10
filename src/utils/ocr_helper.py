from rapidocr_onnxruntime import RapidOCR
from pathlib import Path
import numpy as np
from PIL import Image

import onnxruntime as ort

class OCRHelper:
    """
    基于 RapidOCR 的文字定位代理 (已升级 GPU 加速模式 V5.3)。
    """

    def __init__(self):
        # 初始化引擎，优先尝试 GPU 加速
        try:
            providers = ort.get_available_providers()
            use_gpu = "CUDAExecutionProvider" in providers
            print(f"[OCR] 可用全量供应商: {providers}")
            
            if use_gpu:
                print("[OCR] 成功检测到 NVIDIA CUDA 环境，正在通过 GPU 启动加速引擎...")
                self.engine = RapidOCR(
                    det_use_cuda=True, 
                    cls_use_cuda=True, 
                    rec_use_cuda=True,
                    device_id=0
                )
            else:
                print("[OCR] 未检测到匹配的 CUDA 核心，回退至 CPU 运行。")
                self.engine = RapidOCR()
        except Exception as e:
            print(f"[OCR] 初始化引擎失败: {e}")
            self.engine = None

    def find_text_coordinates(self, image_path: Path, target_text: str):
        """
        在指定图片中寻找目标文字。
        返回: (center_x, center_y) 像素坐标，如果未找到则返回 None。
        """
        if not self.engine:
            return None

        # 1. 运行 OCR
        try:
            import time
            start_time = time.time()
            results, elapse = self.engine(str(image_path))
            total_time = (time.time() - start_time) * 1000
            if results is None:
                return None
            print(f"[OCR] 扫描耗时: {total_time:.1f}ms (引擎原生耗时: {elapse})")
        except Exception as e:
            print(f"[OCR] 识别过程异常: {e}")
            return None

        # 2. 遍历结果进行语义匹配
        # RapidOCR 返回格式通常为: [[[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], "text", confidence], ...]
        for box, text, score in results:
            print(f"[OCR] Detected: '{text}' (Score: {score:.2f})")
            if target_text in text:
                start_idx = text.find(target_text)
                end_idx = start_idx + len(target_text)
                total_len = len(text)
                
                # 获取矩形坐标
                # box 顺序: [左上, 右上, 右下, 左下]
                p_tl = box[0]
                p_tr = box[1]
                p_bl = box[3]
                
                # 宽度和高度
                full_width = p_tr[0] - p_tl[0]
                height = p_bl[1] - p_tl[1]
                
                # 核心修正：计算单个字符的大致像素宽度
                char_width = full_width / total_len
                
                # 计算中心点比例
                center_ratio = (start_idx + end_idx) / (2 * total_len)
                
                # 映射到物理坐标
                # V10.0 零调参方案：纯数学比例映射
                # 无需 +15 或 +30 偏移。直接通过 (字符索引 / 总长度) 计算目标项的几何中点。
                # 这种方法天生对齐，因为它将 OCR 框的每一个像素按比例分配给字符，天然包含了图标等冗余空间。
                res_x = int(p_tl[0] + (full_width * center_ratio))
                res_y = int(p_tl[1] + (height / 2))
                
                print(f"[OCR] Match: '{target_text}' at {start_idx}. Pure Mathematical Pixels: ({res_x}, {res_y})")
                return (res_x, res_y)

        return None

# 导出全局实例（延迟加载）
_helper = None
def get_ocr_helper():
    global _helper
    if _helper is None:
        _helper = OCRHelper()
    return _helper
