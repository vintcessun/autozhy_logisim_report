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
        
        # 统一路径管理
        self.project_root = Path(__file__).parents[2]
        self.prompt_dir = self.project_root / "prompts"

    async def run(self, task: TaskRecord, template_path: Path = None) -> TaskRecord:
        """运行双核协作设计循环"""
        
        # 1. 解析上下文 (target.circ + info.txt)
        analysis = "待解析"
        from logisim_logic import load_project, extract_logical_circuit
        try:
            if template_path and template_path.exists():
                proj = load_project(str(template_path))
                # 兼容 RawProject 的属性访问
                main_name = proj.main.name if proj.main else proj.circuits[0].name
                circ = proj.circuit(main_name)
                analysis = f"当前电路组件数: {len(circ.components)}\n逻辑拓扑：{extract_logical_circuit(circ, project=proj)}"
            else:
                analysis = "从空电路开始设计"
                # 创建临时空文件
                template_path = self.project_root / "workspace" / "empty_template.circ"
                template_path.parent.mkdir(parents=True, exist_ok=True)
                if not template_path.exists():
                    from logisim_logic import RawProject, RawCircuit, RawMain, save_project
                    empty_proj = RawProject(
                        root_attrs={"source": "5.0", "version": "1.0"},
                        root_text="",
                        circuits=[RawCircuit(name="main")], 
                        main=RawMain(name="main")
                    )
                    save_project(empty_proj, template_path)
        except Exception as e:
            analysis = f"解析电路上下文失败: {e}"

        # 2. Pro 阶段：制定架构策略
        self._log("\n" + "="*50 + "\n[Pro] 正在制定设计策略与架构分解...\n" + "="*50)
        design_spec = await self._generate_strategy(task.task_name, task.analysis_raw, analysis, task.target_subcircuit)
        self._log(f"[Pro] 策略生成完毕：\n{design_spec}\n")

        # 3. Flash 阶段：执行代码实现与内部自愈闭环
        self._log("\n" + "="*50 + "\n[Flash] 启动代码实现与内部自愈闭环...\n" + "="*50)
        final_script = await self._execution_loop(design_spec, template_path)
        
        if final_script == "FAILED":
            task.status = "failed"
            task.analysis_raw = "Flash 内部自愈失败，无法通过内生真值表校验。"
            return task

        # 4. 最终物理构建确认
        task.source_circ = [str(template_path.with_name(f"{task.task_name}_design.circ"))]
        task.status = "finished"
        return task

    def _log(self, message: str):
        """写入日志文件"""
        with open("synthesis_log.txt", "a", encoding="utf-8") as f:
            f.write(message + "\n")
        print(message)

    def _load_prompt(self, path: Path, **kwargs) -> str:
        """读取并格式化提示词"""
        if not path.exists():
            return f"Prompt file not found: {path}"
        content = path.read_text(encoding="utf-8")
        # 简单的替换占位符
        for k, v in kwargs.items():
            content = content.replace(f"{{{k}}}", str(v))
        return content

    async def _generate_strategy(self, task_name: str, goal: str, context: str, target_subcircuit: str = None) -> str:
        """Pro 决策逻辑"""
        prompt_path = self.prompt_dir / "design" / "strategy.txt"
        
        # 增强目标子电路上下文
        target_info = f"\n任务明确要求修改的子电路名称为: {target_subcircuit}" if target_subcircuit else ""
        full_goal = f"{goal}{target_info}"
        
        prompt = self._load_prompt(prompt_path, goal=full_goal, context=context)
        
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
        prompt_path = self.prompt_dir / "design" / "execution.txt"
        history = []
        
        for attempt in range(self.max_internal_retries):
            self._log(f"  [Flash] 尝试次数: {attempt + 1}")
            
            # 动态构建 Flash 提示词（包含当前 spec）
            initial_prompt = self._load_prompt(prompt_path, spec=spec)
            
            if attempt == 0:
                history = [types.Content(role="user", parts=[types.Part(text=initial_prompt)])]
            
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
                if response.candidates and response.candidates[0].content.parts:
                    content = "".join([p.text for p in response.candidates[0].content.parts if p.text])

            if not content.strip():
                self._log("  [Error] 模型返回了空内容。")
                history.append(types.Content(role="user", parts=[types.Part(text="你返回了空内容。请务必输出符合模板的 Python 代码块！")]))
                continue

            script_match = re.search(r"```python\n(.*?)\n```", content, re.S)
            script = script_match.group(1) if script_match else content.strip()
            
            # 尝试构建
            out_path = target_path.with_name(f"temp_design_{attempt}.circ")
            if self._build_physical_circuit(script, target_path, out_path):
                # 调用 Internal Verifier 进行自检
                report = self_verify_cla(str(out_path))
                self._log(f"  [Internal Test] {report}")
                
                if "SUCCESS" in report:
                    return script
                else:
                    self._log(f"  [FAIL] 内部真值表报错。")
                    history.append(types.Content(role="user", parts=[types.Part(text=f"内部真值表校验失败！请分析报错并从物理电路连线角度修复（检查 Splitter 索引和隧道名称）：\n{report}")]))
            else:
                self._log("  [Build Error] 物理构建失败。请检查端口名、属性字符串格式以及 finalize_design 是否正确调用。")
                history.append(types.Content(role="user", parts=[types.Part(text="电路物理构建失败！请确保你遵守了端口命名规范，并且代码能够被 exec() 成功执行。不要包含任何非 Logisim 实现代码。")]))
                
        return "FAILED"

    def _build_physical_circuit(self, script: str, template_path: Path, out_path: Path) -> bool:
        """物理执行 Logic Logic 脚本 (兼容新版 ProjectFacade 流程)"""
        try:
            import logisim_logic
            from logisim_logic import ProjectFacade, component
            from logisim_logic.rebuild_support import (
                add_component, add_tunnel, add_tunnel_to_port, add_tunnel_on_port,
                connect_points_routed, connect_ports_routed
            )
            
            def normalize_project_source(path):
                import re
                p = Path(path)
                text = p.read_text(encoding="utf-8")
                updated = re.sub(r'(<project\s+source=")([^"]+?)(")', r"\g<1>2.15.0\3", text, count=1)
                if updated != text:
                    p.write_text(updated, encoding="utf-8")

            exec_globals = {
                "logisim_logic": logisim_logic, 
                "ProjectFacade": ProjectFacade,
                "component": component,
                "add_component": add_component,
                "add_tunnel": add_tunnel,
                "add_tunnel_to_port": add_tunnel_to_port,
                "add_tunnel_on_port": add_tunnel_on_port,
                "connect_points_routed": connect_points_routed,
                "connect_ports_routed": connect_ports_routed,
                "template_path": str(template_path),
                "out_path": str(out_path),
                "normalize_project_source": normalize_project_source,
                "Path": Path,
                "__builtins__": __builtins__
            }
            exec_locals = {}
            exec(script, exec_globals, exec_locals)
            
            if "build_circuit" in exec_locals:
                exec_locals["build_circuit"](str(template_path), str(out_path))
            
            return out_path.exists()
        except Exception as e:
            self._log(f"  [Build Error] {e}")
            import traceback
            self._log(traceback.format_exc())
            return False
