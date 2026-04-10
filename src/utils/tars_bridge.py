import base64
import asyncio
from io import BytesIO
from pathlib import Path
import ollama
import pyautogui
import re
from ui_tars.action_parser import parse_action_to_structure_output, parsing_response_to_pyautogui_code
from .config_loader import OllamaConfig
from .ocr_helper import get_ocr_helper

class TarsBridge:
    """UI-TARS 桥接器，支持 OCR 辅助对齐功能 (V5.0)"""

    def __init__(self, config: OllamaConfig):
        self.config = config
        self.client = ollama.Client(host=config.endpoint)

    def get_screen_size(self):
        """获取当前屏幕物理尺寸"""
        return pyautogui.size()

    async def perform_visual_action(self, instruction: str, screenshot_path: Path, execute: bool = True, window_pos: tuple = (0, 0)) -> bool:
        """
        核心执行流：语义识别 -> [OCR 校准] -> 物理点击
        
        Args:
            instruction: 指令内容
            screenshot_path: 截图路径
            execute: 是否执行 PyAutoGUI 点击
            window_pos: 窗口的左上角全局坐标 (x, y)，用于 OCR 坐标校准
        """
        print(f"[TARS] 正在处理指令: {instruction}")
        
        # 1. 甄别是否包含引号，包含引号则触发 OCR 预对齐 (V5.0+)
        target_text = None
        if '"' in instruction or "'" in instruction:
            import re
            matches = re.findall(r"['\"](.*?)['\"]", instruction)
            if matches:
                target_text = matches[0]
        
        if target_text:
            print(f"[TARS] 检测到潜在 OCR 目标课题: '{target_text}'，正在进行物理扫描...")
            ocr_coordinate = get_ocr_helper().find_text_coordinates(screenshot_path, target_text)
            
            if ocr_coordinate:
                # 方案 A: 物理主导 -> 使用 OCR 算出的像素坐标 + 窗口偏移
                local_x, local_y = ocr_coordinate
                real_x = window_pos[0] + local_x
                # V10.0: 恢复纯物理中心对齐，依靠 OCR 框的原生高度
                real_y = window_pos[1] + local_y
                
                print(f"[TARS] OCR 物理对齐成功! 坐标锁定: ({real_x}, {real_y})")
                
                # V6.4: 使用原子级鼠标事件，防止 DPI 映射丢失信号
                pyautogui.moveTo(real_x, real_y)
                await asyncio.sleep(0.2) 
                pyautogui.mouseDown(real_x, real_y)
                await asyncio.sleep(0.1)
                pyautogui.mouseUp(real_x, real_y)
                return True

        # --- 策略 2: VLM 视觉推理 (VLM Inference) ---
        with open(screenshot_path, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            "You are a GUI agent. You must respond with EXACTLY one Thought and one Action.\n"
            "Action Format: click(start_box='(x,y)')\n\n"
            f"Instruction: {instruction}\n"
            "Thought: "
        )
        
        try:
            response = self.client.generate(
                model=self.config.model_name,
                prompt=prompt,
                images=[img_base64],
                stream=False
            )
            response_text = response['response'].strip()
            print(f"[UI-TARS] 模型原始响应: {response_text}")
        except Exception as e:
            print(f"[UI-TARS] Ollama 异常: {e}")
            return False

        # --- 策略 3: 解析与坐标合成 (Coordinate Synthesis) ---
        try:
            # 基础 AI 坐标解析
            parsed_dict = parse_action_to_structure_output(
                response_text,
                factor=1000,
                origin_resized_height=height,
                origin_resized_width=width,
                model_type="qwen25vl" 
            )
            
            p = None
            if ocr_coordinate:
                # 方案 A: 物理主导 -> 使用 OCR 算出的像素坐标
                real_x, real_y = ocr_coordinate
                print(f"[TARS] 最终执行物理锁定坐标: ({real_x}, {real_y})")
            elif parsed_dict:
                # 方案 B: 视觉主导 -> 使用 AI 归一化坐标转换
                action = parsed_dict[0] if isinstance(parsed_dict, list) else parsed_dict
                # 探测坐标键位结构
                if 'point' in action:
                    p = action['point']
                elif 'action_inputs' in action and 'start_box' in action['action_inputs']:
                    box_str = action['action_inputs']['start_box']
                    match = re.search(r"\((\d+),\s*(\d+)\)", response_text)
                    if match:
                        p = [int(match.group(1)), int(match.group(2))]
                
                if p:
                    real_x = int(p[0] / 1000 * width)
                    real_y = int(p[1] / 1000 * height)
                    print(f"[TARS] AI 推理映射坐标: {p} -> Pixels: ({real_x}, {real_y})")
                else:
                    return False
            else:
                return False

            # --- 最终执行 (Execution) ---
            if execute:
                old_failsafe = pyautogui.FAILSAFE
                try:
                    pyautogui.FAILSAFE = False
                    pyautogui.click(real_x, real_y, button='left', duration=0.5)
                finally:
                    pyautogui.FAILSAFE = old_failsafe
                
            return True
        except Exception as e:
            print(f"[UI-TARS] 合成动作失败: {e}")
            
        return False
