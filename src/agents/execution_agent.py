import asyncio
import re
from pathlib import Path
from google.genai import types
from ..utils.ai_utils import retry_llm_call
from ..utils.tool_definitions import tools_list

class ExecutionAgent:
    """
    Flash 模型：负责具体代码实现与内部校验闭环。
    具有全量工具权限，尤其是 python_interpreter 进行自检。
    """
    def __init__(self, client, model_id: str, max_internal_retries: int = 5):
        self.client = client
        self.model_id = model_id
        self.max_internal_retries = max_internal_retries

    async def generate_and_verify_circuit(self, design_spec: str, target_path: Path) -> str:
        """
        根据规格和反馈，在内部闭环直到自检通过。
        返回最终生成的 Python 脚本。
        """
        history = [
            {"role": "user", "content": f"根据以下设计规格，编写 python 脚本构建 16 位 CLA 电路：\n{design_spec}"}
        ]
        
        attempt = 0
        while attempt < self.max_internal_retries:
            attempt += 1
            print(f"--- [Flash] Attempt {attempt} to build and self-verify...")
            
            config = types.GenerateContentConfig(
                tools=tools_list,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=False)
            )
            
            # 使用统一的重试机制
            response = await retry_llm_call(
                self.client.models.generate_content,
                model=self.model_id,
                contents=history,
                config=config
            )
            
            # 提取脚本
            content = response.text
            script_match = re.search(r"```python\n(.*?)\n```", content, re.S)
            script = script_match.group(1) if script_match else content.strip()
            
            # 准备临时运行并自检
            # 第一步：物理构建电路
            build_success = self._try_build(script, target_path)
            if not build_success:
                history.append({"role": "assistant", "content": content})
                history.append({"role": "user", "content": "脚本执行构建失败，请检查语法和 LogicCircuitBuilder 的调用方式。"})
                continue

            # 第二步：调用工具进行自检
            # 注意：Flash 模型应在其思考过程中通过 Tool 调用 self_verify_cla
            # 我们在这里模拟强制自检反馈，或者让模型主动自检。
            # 为了保证流程严密，我们在此处为模型提供一个“强制自检结果”
            
            from src.utils.internal_verifier import self_verify_cla
            verify_report = self_verify_cla(str(target_path))
            print(f"--- [Internal Feedback] {verify_report}")
            
            if "SUCCESS" in verify_report:
                return script
            else:
                history.append({"role": "assistant", "content": content})
                history.append({"role": "user", "content": f"内部自检未通过！请根据以下真值表错误修正逻辑：\n{verify_report}"})
                
        return "FAILED_TO_SELF_VERIFY"

    def _try_build(self, script: str, target_path: Path) -> bool:
        """尝试运行脚本生成电路文件"""
        try:
            import logisim_logic
            from logisim_logic import save_project, RawMain, RawProject
            
            exec_globals = {
                "logisim_logic": logisim_logic, 
                "LogicCircuitBuilder": logisim_logic.LogicCircuitBuilder
            }
            exec_locals = {}
            exec(script, exec_globals, exec_locals)
            
            result_circuit = None
            if "build_circuit" in exec_locals:
                result_circuit = exec_locals["build_circuit"]()
            elif "circuit" in exec_locals:
                result_circuit = exec_locals["circuit"]
            
            if not result_circuit:
                return False
                
            # 保存
            new_project = RawProject(circuits=[result_circuit], main=RawMain(name=result_circuit.name), root_attrs=[], root_text=[])
            save_project(new_project, target_path)
            return True
        except Exception as e:
            print(f"Build error: {e}")
            return False
