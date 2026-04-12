import asyncio
import json
import re
from pathlib import Path
from google import genai
from src.core.models import TaskRecord
from src.utils.sim_runner import LogisimEmulator

class VerificationAgent:
    """具备动态生成 WebSocket JSON API 动作序列的验证智能体 (Headless API 版)"""

    def __init__(self, config, client: genai.Client):
        self.config = config
        self.client = client
        self.emulator = None

    async def run(self, task: TaskRecord, circ_path: Path) -> TaskRecord:
        """执行验证流程 (WebSocket 版)"""
        if not self.emulator:
            self.emulator = LogisimEmulator(self.config, self.client)
            
        print(f"[Verification] 正在连接并加载电路: {circ_path}")
        success = await self.emulator.launch_and_initialize(str(circ_path))
        if not success:
            task.status = "failed"
            task.analysis_raw = "无法连接或加载电路，请确保后端服务 (ws://localhost:9924/ws) 正在运行。"
            return task

        save_dir = Path("output") / f"{task.task_name}.assets"
        save_dir.mkdir(parents=True, exist_ok=True)
            
        # 1. 尝试让大模型将自然语言分析转换为具体的 API 指令列表
        # 我们向模型提供电路可用的 IO (用于更好的语义映射)
        io_resp = await self.emulator.send_command("get_io")
        io_info = io_resp.get("payload", {}) if isinstance(io_resp, dict) else {}
        
        blueprint = await self._generate_api_blueprint(task, io_info)
        
        # 2. 执行每一个指令
        print(f"[Verification] 开始执行 API 指令序列...")
        for item in blueprint:
            action = item.get("action")
            print(f"[Run API] >> {item}")
            
            # 从字典中剔除 'step' 这类大模型擅自加上的无关字段以免混淆 kwargs
            kwargs = {k: v for k, v in item.items() if k not in ("action", "step", "reason")}
            
            resp = await self.emulator.send_command(action, **kwargs)
            if isinstance(resp, dict) and resp.get("status") == "error":
                print(f"[API Error] 执行指令失败: {resp.get('message')}")
        
        # 3. 最终抓图与验证
        # 我们直接请求截图
        print("[Verification] 正在保存最终状态截图...")
        snap_resp = await self.emulator.send_command("get_screenshot", width=1920, height=1080)
        
        output_path = save_dir / "output.png"
        if isinstance(snap_resp, dict) and snap_resp.get("status") == "ok" and "binary" in snap_resp:
            output_path.write_bytes(snap_resp["binary"])
            await self._crop_image_via_llm(output_path)
            task.assets.append(str(output_path))
        else:
            print("[Warning] 获取截图失败。")
            
        # 如果需要模型进行最终解释的话，可把 output.png 提供给它
        import PIL.Image
        if output_path.exists():
            explanation = self.client.models.generate_content(
                model=self.config.gemini.model_pro,
                contents=[PIL.Image.open(output_path), f"根据执行任务：{task.analysis_raw}，请简要分析结果截图。"]
            ).text.strip()
        else:
            explanation = "未获得有效截图输出。"
            
        task.status = "finished"
        task.analysis_raw = explanation
            
        return task

    async def _crop_image_via_llm(self, image_path: Path):
        """[V8] 优化单位化裁剪：降低灰色边框检测阈值，提高识别灵敏度"""
        import PIL.Image
        import numpy as np
        try:
            with PIL.Image.open(image_path) as img:
                img_rgb = img.convert('RGB')
                data = np.array(img_rgb)
                width, height = img_rgb.size
            
            # --- 阶段 1: 扫描灰色显著线 ---
            # 颜色匹配规则：R~=G~=B, 且不全白全黑
            gray_mask = (np.abs(data[:,:,0].astype(int) - data[:,:,1].astype(int)) < 10) & \
                        (np.abs(data[:,:,1].astype(int) - data[:,:,2].astype(int)) < 10) & \
                        (data[:,:,0] > 100) & (data[:,:,0] < 230)
            
            # 水平方向扫描：如果一行中大部分像素符合灰色，由于边框可能包含文字，我们将阈值降低到 15%
            h_lines = []
            for y in range(height):
                ratio = np.mean(gray_mask[y, :])
                if ratio > 0.15: 
                    h_lines.append(y)
            
            # 垂直方向扫描
            v_lines = []
            for x in range(width):
                ratio = np.mean(gray_mask[:, x])
                if ratio > 0.15:
                    v_lines.append(x)

            def group_lines(lines):
                if not lines: return []
                groups = []
                current = [lines[0]]
                for i in range(1, len(lines)):
                    if lines[i] - lines[i-1] <= 5: # 扩宽线间距容忍度
                        current.append(lines[i])
                    else:
                        groups.append(int(np.mean(current)))
                        current = [lines[i]]
                groups.append(int(np.mean(current)))
                return groups

            y_borders = group_lines(h_lines)
            x_borders = group_lines(v_lines)
            
            # 补充图像物理边界
            y_parts = sorted(list(set([0] + y_borders + [height])))
            x_parts = sorted(list(set([0] + x_borders + [width])))
            
            print(f"[Verification] 检测到水平边框: {y_borders}, 垂直边框: {x_borders}")

            # --- 阶段 2: 大模型交互筛选单元 ---
            prompt = f"""You are an automated circuit validator. The Logisim canvas is partitioned by gray border lines.
Grid Configuration:
- Horizontal Borders at Y: {y_parts} (Indices 0 to {len(y_parts)-1})
- Vertical Borders at X: {x_parts} (Indices 0 to {len(x_parts)-1})

The image is composed of square/rectangular 'Units' between these borders.
Row R is the area between Y[R] and Y[R+1].
Col C is the area between X[C] and X[C+1].

Task: Identify the [row, col] indices for ALL units that contain active circuit components (buttons, wires, LEDs, logic gates). 
Ignore marginal units that are empty or contain only the simulator's background texture.

Return ONLY a JSON list, e.g., [[0, 0], [0, 1]]."""

            res = self.client.models.generate_content(
                model=self.config.gemini.model_pro,
                contents=[PIL.Image.open(image_path), prompt]
            )
            
            raw = res.text.strip()
            # 提取 JSON
            json_match = re.search(r"(\[.*\])", raw, re.DOTALL)
            selected_units = json.loads(json_match.group(1)) if json_match else []
            
            if not selected_units:
                print(f"[Verification] AI 响应不包含单元列表，保持原图。回复: {raw[:30]}...")
                return

            # --- 阶段 3: 计算裁剪坐标与 20px 扩展 ---
            y1_final, y2_final = height, 0
            x1_final, x2_final = width, 0
            
            found_valid = False
            for r, c in selected_units:
                try:
                    if r < len(y_parts)-1 and c < len(x_parts)-1:
                        y1_final = min(y1_final, y_parts[r])
                        y2_final = max(y2_final, y_parts[r+1])
                        x1_final = min(x1_final, x_parts[c])
                        x2_final = max(x2_final, x_parts[c+1])
                        found_valid = True
                except: continue

            if not found_valid:
                print("[Verification] 所选单元超界，放弃裁剪。")
                return

            # 应用 20 像素向外扩展
            padding = 20
            crop_left = max(0, x1_final - padding)
            crop_top = max(0, y1_final - padding)
            crop_right = min(width, x2_final + padding)
            crop_bottom = min(height, y2_final + padding)

            with PIL.Image.open(image_path) as img:
                final_cropped = img.crop((crop_left, crop_top, crop_right, crop_bottom))
                final_cropped.save(image_path)
                print(f"[Verification] 单位化裁剪成功。选中单元: {len(selected_units)}, 最终尺寸: {final_cropped.size}")

        except Exception as e:
            print(f"[Verification] 单位化裁剪失败: {e}")

    async def _generate_api_blueprint(self, task, io_info) -> list:
        """使用大模型将任务映射为 API 动作脚本"""
        prompt = f"""你是一个 Logisim 自动化测试专家。请将以下自然语言任务拆解为 Logisim Headless WebSocket API 可以认识的动作序列。

【任务描述】：{task.analysis_raw}

【当前电路的可用 I/O 引脚】：
{json.dumps(io_info, ensure_ascii=False)}

【支持的 API 动作列表】：
- switch_circuit: {{ "action": "switch_circuit", "name": "电路名" }}
- set_value: {{ "action": "set_value", "target": "引脚名称", "value": "十进制或16进制如0xAA" }}
- tick_until: {{ "action": "tick_until", "target": "引脚名称", "expected": "0x1", "clock": "可选时钟引脚名称", "max": 100 }}
- get_value: {{ "action": "get_value", "target": "引脚名称" }}

请输出 JSON 格式，如下所示：
```json
[
   {{ "action": "switch_circuit", "name": "..." }},
   {{ "action": "set_value", "target": "...", "value": "..." }},
   {{ "action": "tick_until", "target": "...", "expected": "...", "clock": "...", "max": 100 }},
   {{ "action": "get_value", "target": "..." }}
]
```
请仅输出 JSON 数组，无需额外说明。
"""
        res = self.client.models.generate_content(model=self.config.gemini.model_pro, contents=prompt)
        try:
            raw = res.text.strip()
            match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
            data = json.loads(match.group(1)) if match else json.loads(raw)
            return data if isinstance(data, list) else [data]
        except Exception as e:
            print(f"[Agent Parsing Error] 返回非合法 JSON 数组: {e}")
            return []

    def close(self):
        if self.emulator: 
            self.emulator.close()

