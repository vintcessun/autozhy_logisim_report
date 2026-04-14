import asyncio
import re
import json
import shutil
from pathlib import Path
from google.genai import types

from ..utils.ai_utils import retry_llm_call
from ..core.models import TaskRecord
from ..utils.sim_runner import LogisimEmulator

class DesignAgent:
    """
    EDA-AI 设计性实验智能体
    职责：
    1. 参考电路截图：通过 LogisimEmulator (WebSocket) 打开并截图。
    2. 电路重命名与归档：将待提交电路拷贝至 output/提交电路/ 并按任务名命名。
    3. 任务拆解：调用 LLM 将设计要求拆解为细粒度的验证子任务。
    """

    def __init__(self, client, config, model_flash: str):
        self.client = client
        self.config = config
        self.model_flash = model_flash
        self.project_root = Path(__file__).parents[2]
        self.prompt_dir = self.project_root / "prompts"

    async def run(
        self,
        task: TaskRecord,
        source_circ_path: Path | None,
        reference_circ_path: Path | None,
    ) -> tuple[TaskRecord, list[TaskRecord]]:
        """主入口：执行截图、拷贝和拆解。"""
        print(f"\n[DesignAgent] 处理任务: {task.task_name}")

        # 1. 参考电路截图
        if reference_circ_path and reference_circ_path.exists():
            await self._screenshot_reference(task, reference_circ_path)

        # 2. 拷贝并重命名目标电路
        if source_circ_path and source_circ_path.exists():
            self._copy_target_circuit(task, source_circ_path)

        # 3. 细粒度任务拆解
        sub_tasks = await self._decompose_to_subtasks(task)

        task.status = "finished"
        return task, sub_tasks

    async def _screenshot_reference(self, task: TaskRecord, ref_path: Path):
        """打开参考电路并截图，保存至 output/实验报告.assets/"""
        emulator = LogisimEmulator(self.config, self.client)
        print(f"[DesignAgent] 正在截图参考电路: {ref_path}")
        
        # 确保使用绝对路径
        abs_ref = ref_path.absolute()
        success = await emulator.launch_and_initialize(str(abs_ref))
        if not success:
            raise RuntimeError(f"Logisim WebSocket 服务未运行或无法加载参考电路: {ref_path}")

        try:
            # 确保资产目录存在
            assets_dir = Path("output") / "实验报告.assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            
            save_path = assets_dir / f"reference_{task.task_name}.png"
            
            # 使用现有接口获取截图
            snap_resp = await emulator.send_command("get_screenshot", width=1920, height=1080)
            if isinstance(snap_resp, dict) and snap_resp.get("status") == "ok" and "binary" in snap_resp:
                save_path.write_bytes(snap_resp["binary"])
                # 将截图路径插入 assets 列表的第一位
                task.assets.insert(0, str(save_path))
                print(f"[DesignAgent] 参考电路截图已保存: {save_path}")
            else:
                print(f"[DesignAgent] 警告: 获取参考电路截图失败: {snap_resp}")
        finally:
            emulator.close()

    def _copy_target_circuit(self, task: TaskRecord, source_path: Path):
        """将电路拷贝到 output/提交电路/ 并命名"""
        submit_dir = Path("output") / "提交电路"
        submit_dir.mkdir(parents=True, exist_ok=True)
        
        target_path = submit_dir / f"{task.task_name}.circ"
        shutil.copy2(source_path, target_path)
        
        # 更新任务记录中的源码路径为新的命名路径
        task.source_circ = [str(target_path)]
        print(f"[DesignAgent] 电路已归档: {target_path}")

    async def _decompose_to_subtasks(self, task: TaskRecord) -> list[TaskRecord]:
        """调用 LLM 进行细粒度验证任务拆解"""
        prompt_path = self.prompt_dir / "design" / "decompose.txt"
        if not prompt_path.exists():
            print(f"[DesignAgent] 错误: 找不到 Prompt 文件 {prompt_path}")
            return []

        prompt_tmpl = prompt_path.read_text(encoding="utf-8")
        prompt = prompt_tmpl.replace("{task_name}", task.task_name or "") \
                            .replace("{section_text}", task.section_text or "") \
                            .replace("{analysis_raw}", task.analysis_raw or "")

        print("[DesignAgent] 正在拆解细粒度验证任务...")
        
        # 使用 Flash 模型进行拆解
        response = await retry_llm_call(
            self.client.models.generate_content,
            model=self.model_flash,
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        raw_content = response.text.strip()
        
        # 提取 JSON 内容
        json_str = ""
        md_match = re.search(r"```json\s*(.*?)\s*```", raw_content, re.DOTALL)
        if md_match:
            json_str = md_match.group(1).strip()
        else:
            bracket_match = re.search(r"(\[.*\])", raw_content, re.DOTALL)
            if bracket_match:
                json_str = bracket_match.group(1).strip()
            else:
                json_str = raw_content

        if not json_str:
            print(f"[DesignAgent] 警告: LLM 返回内容无法识别为 JSON 列表: {raw_content[:200]}")
            return []

        try:
            items = json.loads(json_str)
            sub_tasks = []
            for item in items:
                sub_task = TaskRecord(
                    task_name=item.get("task_name", f"{task.task_name} - 子任务"),
                    task_type="verification",
                    # 继承父任务的电路（即归档后的命名电路）
                    source_circ=task.source_circ,
                    analysis_raw=item.get("description", ""),
                    # 继承原始 info 文字
                    section_text=task.section_text,
                    target_subcircuit=task.target_subcircuit,
                    experiment_objective=task.experiment_objective,
                )
                sub_tasks.append(sub_task)
            print(f"[DesignAgent] 成功拆解出 {len(sub_tasks)} 个验证子任务。")
            return sub_tasks
        except Exception as e:
            print(f"[DesignAgent] 错误: JSON 解析失败: {e}\n原文: {json_str[:200]}")
            return []
