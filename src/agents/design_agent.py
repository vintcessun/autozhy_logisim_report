import asyncio
import re
import sys
from pathlib import Path
from google.genai import types
from ..utils.ai_utils import retry_llm_call
from ..utils.tool_definitions import tools_list
from ..utils.internal_verifier import self_verify_cla
from ..core.models import TaskRecord

class DesignAgent:
    """
    重塑后的 DesignAgent (Pro/Flash 协作架构)。
    职责：基于 Pro 的策略和 Flash 的执行，通过内部自检闭环生成 100% 对齐的电路。
    """

    def __init__(self, client, model_pro: str, model_flash: str = None, max_internal_retries: int = 5):
        self.client = client
        self.model_pro = model_pro
        self.model_flash = model_flash or model_pro # 默认回退
        self.max_internal_retries = max_internal_retries
        
        # 确保路径
        vendor_path = str(Path(__file__).parents[1] / "vendor")
        if vendor_path not in sys.path:
            sys.path.append(vendor_path)

    async def run(self, task: TaskRecord, template_path: Path) -> TaskRecord:
        """运行双核协作设计循环"""
        
        # 1. 解析上下文 (target.circ + info.txt)
        analysis = "待解析"
        from logisim_logic import load_project, extract_logical_circuit
        try:
            if template_path.exists():
                proj = load_project(str(template_path))
                circ = proj.main_circuit or proj.circuits[0]
                analysis = f"当前电路组件数: {len(circ.components)}\n逻辑拓扑：{extract_logical_circuit(circ, project=proj)}"
        except Exception as e:
            analysis = f"解析电路上下文失败: {e}"

        # 2. Pro 阶段：制定架构策略
        self._log("\n" + "="*50 + "\n[Pro] 正在制定设计策略与架构分解...\n" + "="*50)
        design_spec = await self._generate_strategy(task.task_name, task.analysis_raw, analysis)
        self._log(f"[Pro] 策略生成完毕：\n{design_spec}\n")

        # 3. Flash 阶段：执行代码实现与内部自愈闭环
        self._log("\n" + "="*50 + "\n[Flash] 启动代码实现与内部自愈闭环...\n" + "="*50)
        final_script = await self._execution_loop(design_spec, template_path)
        
        if final_script == "FAILED":
            task.status = "failed"
            task.analysis_raw = "Flash 内部自愈失败，无法通过内生真值表校验。"
            return task

        # 4. 最终物理构建 (由 Flash 逻辑生成)
        task.source_circ = [str(template_path.with_name(f"{task.task_name}_design.circ"))]
        task.status = "finished"
        return task

    def _log(self, message: str):
        """写入日志文件"""
        with open("synthesis_log.txt", "a", encoding="utf-8") as f:
            f.write(message + "\n")
        print(message)

    async def _generate_strategy(self, task_name: str, goal: str, context: str) -> str:
        """Pro 决策逻辑"""
        prompt = f"""你是一个高级数字电路架构师 (Pro)。
任务目标：设计或修复一个 16位 先行进位加法器 (CLA)。
参考背景：{goal}
电路现状：{context}

你的职责：
1. 调研 16位 CLA 的分层逻辑公式。
2. 为 Flash 模型提供清晰的布线指导和组件清单。
3. 必须通过 search_web 确认公式无误。
4. 可以使用 python_interpreter 验证数学逻辑。

请输出详细的【设计规格说明书】。
"""
        config = types.GenerateContentConfig(
            tools=tools_list,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False)
        )
        response = await retry_llm_call(
            self.client.models.generate_content,
            model=self.model_pro,
            contents=prompt,
            config=config
        )
        return response.text.strip()

    async def _execution_loop(self, spec: str, target_path: Path) -> str:
        """Flash 执行逻辑：重写 -> 自检 -> 纠错"""
        # 读取核心 API 规范
        api_spec = Path("prompts/design/design_logic.txt").read_text(encoding="utf-8")
        
        initial_prompt = f"""你是一个电路实现专家 (Flash)。
任务：根据架构师 (Pro) 的规格说明书，使用 `logisim_logic` 库编写 Python 构建脚本。

### 强制执行规则：
1. **绝对不要** 编写 Python 类或逻辑模拟代码。
2. **必须** 严格遵守以下 `logisim_logic` API 规范。
3. **必须** 包含一个 `build_circuit()` 函数并返回 `builder.build()` 的结果。
4. **必须** 使用 `force_tunnel=True` 进行所有连接。

### 核心 API 规范
{api_spec}

### 架构师规格说明书
{spec}

### 代码模板 (必须以此格式输出):
```python
from logisim_logic import LogicCircuitBuilder

def build_circuit():
    builder = LogicCircuitBuilder("CLA16", allow_tunnel_fallback=True)
    # 你的实现代码
    return builder.build()
```
请直接输出代码块。
"""
        history = [types.Content(role="user", parts=[types.Part(text=initial_prompt)])]
        
        for attempt in range(self.max_internal_retries):
            self._log(f"  [Flash] 尝试次数: {attempt + 1}")
            
            # 关键：Flash 实现阶段关闭工具，强制模型返回文本代码
            config = types.GenerateContentConfig(
                tools=[], # 禁用工具
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
            )
            response = await retry_llm_call(
                self.client.models.generate_content,
                model=self.model_flash,
                contents=history,
                config=config
            )
            
            # 保存助手回复到历史
            if response.candidates and response.candidates[0].content:
                history.append(response.candidates[0].content)
            
            # 提取脚本
            content = response.text or ""
            
            if not content.strip():
                # 再次检查 candidate 列表
                if response.candidates and response.candidates[0].content.parts:
                    content = "".join([p.text for p in response.candidates[0].content.parts if p.text])

            if not content.strip():
                self._log("  [Error] 模型返回了空内容。")
                history.append(types.Content(role="user", parts=[types.Part(text="你返回了空内容。请务必输出符合模板的 Python 代码块！")]))
                continue

            script_match = re.search(r"```python\n(.*?)\n```", content, re.S)
            script = script_match.group(1) if script_match else content.strip()
            
            self._log(f"  [Flash] 提取的脚本片段 (前100字): {script[:100]}...")
            
            # 尝试构建
            out_path = target_path.with_name(f"temp_design_{attempt}.circ")
            if self._build_physical_circuit(script, out_path):
                # 调用 Internal Verifier 进行自检
                report = self_verify_cla(str(out_path))
                self._log(f"  [Internal Test] {report}")
                
                if "SUCCESS" in report:
                    # 最终保存到目标位置
                    import shutil
                    final_file = target_path.with_name(f"16位快速加法器设计_design.circ")
                    shutil.copy(out_path, final_file)
                    self._log(f"  [SUCCESS] 已生成最终文件: {final_file}")
                    return script
                else:
                    self._log(f"  [FAIL] 内部真值表报错。")
                    history.append(types.Content(role="user", parts=[types.Part(text=f"内部真值表校验失败！请分析报错并从物理电路连线角度修复（检查 Splitter 索引和隧道名称）：\n{report}")]))
            else:
                self._log("  [Build Error] 物理构建失败。请检查端口名、属性字符串格式以及 builder.build() 是否正确调用。")
                history.append(types.Content(role="user", parts=[types.Part(text="电路物理构建失败！请确保你遵守了端口命名规范，并且代码能够被 exec() 成功执行。不要包含任何非 Logisim 实现代码。")]))
                
        return "FAILED"

    def _build_physical_circuit(self, script: str, out_path: Path) -> bool:
        """物理执行 Logic Logic 脚本"""
        try:
            import logisim_logic
            from logisim_logic import RawProject, RawMain, save_project
            exec_globals = {"logisim_logic": logisim_logic, "LogicCircuitBuilder": logisim_logic.LogicCircuitBuilder}
            exec_locals = {}
            exec(script, exec_globals, exec_locals)
            
            circ = exec_locals.get("build_circuit")() if "build_circuit" in exec_locals else exec_locals.get("circuit")
            if not circ: 
                self._log("  [Build Error] 脚本未返回有效的电路对象。")
                return False
            
            new_project = RawProject(circuits=[circ], main=RawMain(name=circ.name), root_attrs=[], root_text=[])
            save_project(new_project, out_path)
            return True
        except Exception as e:
            self._log(f"  [Build Error] {e}")
            return False
