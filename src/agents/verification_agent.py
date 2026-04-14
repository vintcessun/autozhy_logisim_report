import asyncio
import json
import re
from pathlib import Path
from google import genai
from src.core.models import TaskRecord
from src.utils.sim_runner import LogisimEmulator

class VerificationAgent:
    """具备动态生成 WebSocket JSON API 动作序列的验证智能体 (Headless API 版)"""

    def __init__(self, config, client: genai.Client, cache=None):
        self.config = config
        self.client = client
        self.cache = cache
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
        print(f"[DEBUG VERIFICATION AGENT PROMPT] {content}")
        return content

    def _sanitize_filename(self,filename: str, replacement: str = "-") -> str:
        # 正则模式匹配所有非法字符
        # 注意：反斜杠 \ 需要在正则中转义为 \\
        pattern = r'[<>:"/\\|?*\x00-\x1F]'
        
        # 将非法字符替换为你指定的分隔符（默认替换为 "-"）
        sanitized = re.sub(pattern, replacement, filename)
        
        # 可选优化：去除两端的空格和点（Windows 不允许文件夹以点或空格结尾）
        return sanitized.strip(" .")

    async def run(self, task: TaskRecord, circ_path: Path) -> TaskRecord:
        """执行验证流程 (WebSocket 版)"""
        # 0. 检查缓存
        if self.cache:
            cached = self.cache.get_task_if_done(task)
            if cached:
                return cached

        if not self.emulator:
            self.emulator = LogisimEmulator(self.config, self.client)
            
        print(f"[Verification] 正在连接并加载电路: {circ_path}")
        success = await self.emulator.launch_and_initialize(str(circ_path))
        if not success:
            task.status = "failed"
            task.analysis_raw = "无法连接或加载电路，请确保后端服务 (ws://localhost:9924/ws) 正在运行。"
            return task

        save_dir = Path("output") / ".assets"
        save_dir.mkdir(parents=True, exist_ok=True)
            
        max_retries = 2
        last_error = None
        blueprint = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                print(f"[Verification] 正在进行第 {attempt} 次自修复重试...")
                # 环境重置：完全关闭并重新连接
                self.emulator.close()
                await self.emulator.launch_and_initialize(str(circ_path))

            # 1. 获取基础上下文
            circuits_resp = await self.emulator.send_command("get_circuits")
            circuit_list = circuits_resp.get("payload", []) if isinstance(circuits_resp, dict) else []
            
            # 2. 识别并切换到目标子电路 (使用 Gemini Flash)
            await self._identify_and_switch_circuit(task, circuit_list)

            io_resp = await self.emulator.send_command("get_io")
            io_info = io_resp.get("payload", {}) if isinstance(io_resp, dict) else {}
            
            # 3. 生成或修复指令
            if attempt == 0:
                blueprint = await self._generate_api_blueprint(task, io_info, circuit_list)
            else:
                blueprint = await self._repair_api_blueprint(task, io_info, circuit_list, blueprint, failed_item, last_error)

            # 4. 执行指令序列
            print(f"[Verification] 开始执行 API 指令序列 (尝试 {attempt+1})...")
            failed_item = None
            for item in blueprint:
                action = item.get("action")
                print(f"[Run API] >> {item}")
                kwargs = {k: v for k, v in item.items() if k not in ("action", "step", "reason")}
                
                resp = await self.emulator.send_command(action, **kwargs)
                if isinstance(resp, dict) and resp.get("status") == "error":
                    failed_item = item
                    last_error = resp.get("message")
                    print(f"[API Error] 指令执行失败: {last_error}")
                    break
            
            if not failed_item:
                print("[Verification] 指令序列全量执行成功。")
                break
        
        if failed_item:
            task.status = "failed"
            task.analysis_raw = f"API 指令序列执行失败，已达重试上限。错误: {last_error}"
            return task

        # 5. 最终抓图与验证
        print("[Verification] 正在保存最终状态截图...")
        snap_resp = await self.emulator.send_command("get_screenshot", width=1920, height=1080)
        
        filtered_taskname = self._sanitize_filename(task.task_name)
        output_path = save_dir / f"{filtered_taskname}.png"
        if isinstance(snap_resp, dict) and snap_resp.get("status") == "ok" and "binary" in snap_resp:
            output_path.write_bytes(snap_resp["binary"])
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

        # 4. 保存缓存
        if self.cache:
            self.cache.save_task(task)

        return task

    async def _generate_api_blueprint(self, task:TaskRecord, io_info, circuit_list) -> list:
        """映射任务为 API 动作脚本：包含完整的 API 协议与电路上下文"""
        prompt_path = self.prompt_dir / "verification" / "blueprint.txt"
        
        prompt = self._load_prompt(
            prompt_path, 
            goal=task.analysis_raw, 
            task_name=task.task_name,
            io_input=json.dumps(io_info["inputs"], ensure_ascii=False),
            io_output=json.dumps(io_info["outputs"], ensure_ascii=False),
            circuit_list=json.dumps(circuit_list, ensure_ascii=False),
            target_subcircuit=task.target_subcircuit or "未指定"
        )
        return await self._call_llm_for_blueprint(prompt)

    async def _identify_and_switch_circuit(self, task: TaskRecord, circuit_list: list):
        """使用 Gemini Flash 确定并切换到目标电路"""
        prompt_path = self.prompt_dir / "verification" / "switch.txt"
        prompt = self._load_prompt(
            prompt_path,
            task_name=task.task_name,
            goal=task.analysis_raw,
            target_subcircuit=task.target_subcircuit or "未指定",
            circuit_list=json.dumps(circuit_list, ensure_ascii=False)
        )
        
        from ..utils.ai_utils import retry_llm_call
        response = await retry_llm_call(
            self.client.models.generate_content,
            model=self.config.gemini.model_flash,
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        try:
            raw = response.text
            extracted = self._extract_json(raw)
            if extracted:
                cmd = json.loads(extracted)
                if isinstance(cmd, list): cmd = cmd[0]
                
                action = cmd.get("action")
                name = cmd.get("name")
                if action == "switch_circuit" and name:
                    print(f"[Verification] 正在切换到电路: {name}")
                    await self.emulator.send_command("switch_circuit", name=name)
                else:
                    print(f"[Warning] 识别出的切换指令无效: {cmd}")
            else:
                print(f"[Warning] 无法从 LLM 响应中提取切换指令: {raw}")
        except Exception as e:
            print(f"[Error] 执行电路识别切换失败: {e}")


    async def _repair_api_blueprint(self, task, io_info, circuit_list, failed_blueprint, failed_item, error_msg) -> list:
        """任务执行失败时的自修复逻辑"""
        prompt_path = self.prompt_dir / "verification" / "repair.txt"
        
        prompt = self._load_prompt(
            prompt_path,
            goal=task.analysis_raw,
            target_subcircuit=task.target_subcircuit or "未指定",
            circuit_list=json.dumps(circuit_list, ensure_ascii=False),
            io_info=json.dumps(io_info, ensure_ascii=False),
            failed_blueprint=json.dumps(failed_blueprint, ensure_ascii=False),
            error_step=json.dumps(failed_item, ensure_ascii=False),
            error_message=error_msg
        )
        return await self._call_llm_for_blueprint(prompt)

    async def _call_llm_for_blueprint(self, prompt: str, max_json_retries: int = 2) -> list:
        """调用 LLM 生成指令序列，并包含 JSON 自修复逻辑"""
        from ..utils.ai_utils import retry_llm_call
        import json as _json

        # 初始调用
        response = await retry_llm_call(
            self.client.models.generate_content,
            model=self.config.gemini.model_pro,
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        raw = response.text

        for attempt in range(max_json_retries + 1):
            extracted = self._extract_json(raw)
            if extracted:
                try:
                    data = _json.loads(extracted)
                    return data if isinstance(data, list) else [data]
                except Exception:
                    pass

            if attempt < max_json_retries:
                print(f"[JSON 自修复] Blueprint 生成第 {attempt+1} 次失败，请求 LLM 修复...")
                repair_prompt = (
                    "以下输出不是合法的 JSON 指令数组，请修正并重新输出，不要包含任何解释文字：\n\n"
                    + raw[:2000]
                )
                response = await retry_llm_call(
                    self.client.models.generate_content,
                    model=self.config.gemini.model_pro,
                    contents=repair_prompt,
                    config={'response_mime_type': 'application/json'}
                )
                raw = response.text
            else:
                print(f"[Agent Parsing Error] 达到最大重试次数，无法解析 JSON: {raw[:200]}")

        return []

    def _extract_json(self, text: str) -> str:
        """从杂乱文本中提取最可能的 JSON 数组或对象"""
        import json as _json
        import re as _re
        text = text.strip()
        
        # 1. 尝试直接解析
        try:
            _json.loads(text)
            return text
        except: pass

        # 2. 尝试提取 ```json ... ``` 或简单的 [ ... ] / { ... }
        for pattern in (r"```json\s*(.*?)\s*```", r"(\[.*\])", r"(\{.*\})"):
            m = _re.search(pattern, text, _re.DOTALL)
            if m:
                candidate = m.group(1).strip()
                try:
                    _json.loads(candidate)
                    return candidate
                except: pass
        return ""

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
