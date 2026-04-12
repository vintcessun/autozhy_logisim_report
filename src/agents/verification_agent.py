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
        self.project_root = Path(__file__).parents[2]
        self.prompt_dir = self.project_root / "prompts"

    def _load_prompt(self, path: Path, **kwargs) -> str:
        """读取并格式化提示词"""
        if not path.exists():
            return f"Prompt file not found: {path}"
        content = path.read_text(encoding="utf-8")
        for k, v in kwargs.items():
            content = content.replace(f"{{{k}}}", str(v))
        return content

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
        io_resp = await self.emulator.send_command("get_io")
        io_info = io_resp.get("payload", {}) if isinstance(io_resp, dict) else {}
        
        blueprint = await self._generate_api_blueprint(task, io_info)
        
        # 2. 执行每一个指令
        print(f"[Verification] 开始执行 API 指令序列...")
        for item in blueprint:
            action = item.get("action")
            print(f"[Run API] >> {item}")
            kwargs = {k: v for k, v in item.items() if k not in ("action", "step", "reason")}
            
            resp = await self.emulator.send_command(action, **kwargs)
            if isinstance(resp, dict) and resp.get("status") == "error":
                print(f"[API Error] 执行指令失败: {resp.get('message')}")
        
        # 3. 最终抓图与验证
        print("[Verification] 正在保存最终状态截图...")
        snap_resp = await self.emulator.send_command("get_screenshot", width=1920, height=1080)
        
        output_path = save_dir / "output.png"
        if isinstance(snap_resp, dict) and snap_resp.get("status") == "ok" and "binary" in snap_resp:
            output_path.write_bytes(snap_resp["binary"])
            await self._crop_image_via_llm(output_path)
            task.assets.append(str(output_path))
        else:
            print("[Warning] 获取截图失败。")
            
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
        """单位化裁剪：外部提示词版"""
        import PIL.Image
        import numpy as np
        try:
            with PIL.Image.open(image_path) as img:
                img_rgb = img.convert('RGB')
                data = np.array(img_rgb)
                width, height = img_rgb.size
            
            # 扫描灰色线 (保持原有逻辑)
            gray_mask = (np.abs(data[:,:,0].astype(int) - data[:,:,1].astype(int)) < 10) & \
                        (np.abs(data[:,:,1].astype(int) - data[:,:,2].astype(int)) < 10) & \
                        (data[:,:,0] > 100) & (data[:,:,0] < 230)
            
            h_lines = [y for y in range(height) if np.mean(gray_mask[y, :]) > 0.15]
            v_lines = [x for x in range(width) if np.mean(gray_mask[:, x]) > 0.15]

            def group_lines(lines):
                if not lines: return []
                groups, current = [], [lines[0]]
                for i in range(1, len(lines)):
                    if lines[i] - lines[i-1] <= 5: current.append(lines[i])
                    else:
                        groups.append(int(np.mean(current)))
                        current = [lines[i]]
                groups.append(int(np.mean(current)))
                return groups

            y_borders = group_lines(h_lines)
            x_borders = group_lines(v_lines)
            y_parts = sorted(list(set([0] + y_borders + [height])))
            x_parts = sorted(list(set([0] + x_borders + [width])))
            
            prompt_path = self.prompt_dir / "verification" / "cropping.txt"
            prompt = self._load_prompt(prompt_path, 
                                      y_parts=y_parts, y_parts_len=len(y_parts)-1,
                                      x_parts=x_parts, x_parts_len=len(x_parts)-1)

            res = self.client.models.generate_content(
                model=self.config.gemini.model_pro,
                contents=[PIL.Image.open(image_path), prompt]
            )
            
            raw = res.text.strip()
            json_match = re.search(r"(\[.*\])", raw, re.DOTALL)
            selected_units = json.loads(json_match.group(1)) if json_match else []
            
            if not selected_units: return

            y1_final, y2_final = height, 0
            x1_final, x2_final = width, 0
            found_valid = False
            for r, c in selected_units:
                if r < len(y_parts)-1 and c < len(x_parts)-1:
                    y1_final, y2_final = min(y1_final, y_parts[r]), max(y2_final, y_parts[r+1])
                    x1_final, x2_final = min(x1_final, x_parts[c]), max(x2_final, x_parts[c+1])
                    found_valid = True

            if not found_valid: return

            padding = 20
            crop = (max(0, x1_final - padding), max(0, y1_final - padding),
                    min(width, x2_final + padding), min(height, y2_final + padding))

            with PIL.Image.open(image_path) as img:
                final_cropped = img.crop(crop)
                final_cropped.save(image_path)
                print(f"[Verification] 单位化裁剪成功。")

        except Exception as e:
            print(f"[Verification] 单位化裁剪失败: {e}")

    async def _generate_api_blueprint(self, task, io_info) -> list:
        """映射任务为 API 动作脚本：外部提示词版"""
        prompt_path = self.prompt_dir / "verification" / "blueprint.txt"
        prompt = self._load_prompt(prompt_path, goal=task.analysis_raw, io_info=json.dumps(io_info, ensure_ascii=False))

        res = self.client.models.generate_content(model=self.config.gemini.model_pro, contents=prompt)
        try:
            raw = res.text.strip()
            match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
            data = json.loads(match.group(1)) if match else json.loads(raw)
            return data if isinstance(data, list) else [data]
        except Exception as e:
            print(f"[Agent Parsing Error] 返回非合法 JSON: {e}")
            return []

    def close(self):
        if self.emulator: 
            self.emulator.close()
