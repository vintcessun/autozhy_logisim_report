import os
import requests
import json
import asyncio
import subprocess
import threading
import traceback
import sys
import inspect
import re
import uuid
from typing import Dict, Any, List
from pathlib import Path


def _log_to_synthesis_log(message: str):
    with open("synthesis_log.txt", "a", encoding="utf-8") as f:
        f.write(message + "\n")


def _sanitize_tool_filename(filename: str, default_name: str = "tool_run.py") -> str:
    candidate = (filename or default_name).strip()
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate)
    candidate = candidate.strip(" ._") or default_name
    if not candidate.endswith(".py"):
        candidate += ".py"
    return candidate


def _run_coroutine_in_thread(coro: Any) -> Any:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result.get("value")


def tool_inventory_circuit(project_path: str, circuit_name: str) -> str:
    """
    (观察阶段工具) 盘点指定电路工程中的所有子电路及其内部组件统计。
    直接从电路源文件提取元数据，返回组件类型、坐标及关键属性（Label, Width 等）。
    用于了解原电路结构，决定哪些组件可以保留，哪些必须删除。
    """
    _log_to_synthesis_log(
        f"    [Tool] Calling tool_inventory_circuit for {circuit_name}"
    )
    from logisim_logic import load_project

    try:
        path = Path(project_path)
        if not path.exists():
            return f"错误：找不到工程文件 {project_path}"

        project = load_project(str(path))
        if not project.has_circuit(circuit_name):
            available = [c.name for c in project.circuits]
            return f"错误：电路 {circuit_name} 不存在。可用电路: {available}"

        circuit = project.circuit(circuit_name)
        report = {
            "circuit_name": circuit_name,
            "component_counts": {},
            "labeled_components": [],
            "all_components": [],
        }

        from collections import Counter

        report["component_counts"] = dict(
            Counter(comp.name for comp in circuit.components)
        )

        for i, comp in enumerate(circuit.components):
            c_info = {
                "id": i,
                "name": comp.name,
                "loc": comp.loc,
                "attrs": comp.attr_map(),
            }
            if comp.get("label"):
                report["labeled_components"].append(c_info)
            report["all_components"].append(c_info)

        return json.dumps(report, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"执行失败: {str(e)}"


def tool_get_geometry(project_path: str, circuit_name: str, component_id: int) -> str:
    """
    (精准观察工具) 获取指定组件的精确几何信息。
    返回组件所有端口的：真实名称（name）、偏移量（offset）、绝对坐标（abs_loc）、位宽（width）和朝向（direction）。
    在布线（connect_ports_routed）前必须调用此工具，以获取准确的引脚位置。
    """
    from logisim_logic import load_project, get_component_geometry

    try:
        project = load_project(project_path)
        circuit = project.circuit(circuit_name)
        if component_id < 0 or component_id >= len(circuit.components):
            return f"错误：组件 ID {component_id} 越界。"

        comp = circuit.components[component_id]
        geom = get_component_geometry(comp, project=project)

        report = {"name": comp.name, "loc": comp.loc, "lib": comp.lib, "ports": []}
        for port in geom.ports:
            abs_point = (comp.loc[0] + port.offset[0], comp.loc[1] + port.offset[1])
            report["ports"].append(
                {
                    "name": port.name,
                    "offset": port.offset,
                    "abs_loc": abs_point,
                    "direction": port.direction,
                    "width": port.width,
                }
            )
        return json.dumps(report, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"执行失败: {str(e)}"


def tool_check_topology(project_path: str, circuit_name: str) -> str:
    """
    (逻辑观察工具) 提取电路的完整逻辑拓扑（网表）。
    返回逻辑实例（Instances）及其端口连接的网络（Nets）。
    用于核查哪些端口实际上连在一起，是否存在悬空导线或未预期的短路。
    """
    from logisim_logic import load_project, extract_logical_circuit

    try:
        project = load_project(project_path)
        circuit = project.circuit(circuit_name)
        logical = extract_logical_circuit(circuit, project=project)

        report = {"instances": [], "nets": []}
        for inst in logical.instances:
            report["instances"].append(
                {
                    "id": inst.id,
                    "kind": inst.kind,
                    "loc": inst.loc,
                    "ports": inst.port_points,
                }
            )
        for net in logical.nets:
            report["nets"].append(
                {
                    "id": net.id,
                    "tunnel_labels": list(net.tunnel_labels),
                    "endpoints": [(ep.instance, ep.port) for ep in net.endpoints],
                }
            )
        return json.dumps(report, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"执行失败: {str(e)}"


def tool_apply_modifications(
    project_path: str, output_path: str, circuit_name: str, script_code: str
) -> str:
    """
    (核心设计执行工具) 执行基于 logisim_logic 库编写的 Python 修改脚本。
    该脚本在预注入了 logisim_logic 核心能力的沙盒中运行。
    可以使用：rs (rebuild_support), ProjectFacade, CircuitEditor 等。
    """
    import logisim_logic
    from logisim_logic import rebuild_support as rs_mod
    from logisim_logic import model as model_mod
    from logisim_logic.high_level import ProjectFacade, CircuitEditor
    from io import StringIO
    from contextlib import redirect_stdout, redirect_stderr

    _log_to_synthesis_log(
        f"--- [EXECUTING SCRIPT IN TOOL] ---\n{script_code}\n----------------------------------"
    )
    try:
        # 确保路径解析正确
        abs_in = str(Path(project_path).absolute())
        abs_out = str(Path(output_path).absolute())

        session = ProjectFacade.load(abs_in)
        if isinstance(session, str):
            err = f"获取 ProjectFacade 失败: 返回了字符串 '{session}' 而非对象"
            _log_to_synthesis_log(f"    [Fatal Error] {err}")
            return err

        editor = session.edit_circuit(circuit_name)
        raw_circ = editor.circuit

        # 注入高层辅助函数，减少智能体幻觉
        def add_instance(name, loc, attrs=None, lib="0"):
            """添加组件实例。"""
            return rs_mod.add_component(raw_circ, name, loc, attrs or {}, lib=lib)

        def connect(p1, p2):
            """
            智能连接两个端口。
            支持:
            - "Inst.Port": 自动根据 label 寻找实例并连接端口。
            - "Label": 自动寻找该 label 的组件 (如 Pin) 并使用其主要端口。
            - (x, y): 坐标点。
            """

            def _resolve(p):
                if isinstance(p, str):
                    if "." in p:
                        inst_name, port_name = p.split(".", 1)
                        inst = next(
                            (
                                c
                                for c in raw_circ.components
                                if (c.label or "").strip() == inst_name
                            ),
                            None,
                        )
                        if inst is None:
                            inst = next(
                                (c for c in raw_circ.components if c.name == inst_name),
                                None,
                            )

                        if inst is None:
                            raise KeyError(f"找不到实例: {inst_name}")
                        return (inst, port_name)
                    else:
                        inst = next(
                            (
                                c
                                for c in raw_circ.components
                                if (c.label or "").strip() == p
                            ),
                            None,
                        )
                        if inst is None:
                            inst = next(
                                (c for c in raw_circ.components if c.name == p), None
                            )

                        if inst is None:
                            raise KeyError(f"找不到实例: {p}")
                        return (inst, "")
                if hasattr(p, "name") and hasattr(p, "loc"):  # 看起来像 RawComponent
                    return (p, "")
                return p

            try:
                r1 = _resolve(p1)
                r2 = _resolve(p2)

                if isinstance(r1, tuple) and isinstance(r2, tuple):
                    # 如果都是 (comp, port)
                    return rs_mod.connect_ports_routed(
                        raw_circ, r1[0], r1[1], r2[0], r2[1], project=session.project
                    )

                # 如果是点
                p1_pt = r1 if isinstance(r1, tuple) else None
                p2_pt = r2 if isinstance(r2, tuple) else None
                if p1_pt and p2_pt:
                    return editor.add_wire(p1_pt, p2_pt)
            except Exception as e:
                _log_to_synthesis_log(f"    [Connect Error] {p1} -> {p2}: {str(e)}")
                raise

            raise ValueError(f"无法解析连接参数: {p1}, {p2}")

        def save_circuit(path=None):
            """保存当前电路到指定路径。"""
            p = path or abs_out
            session.save(str(Path(p).absolute()))
            return f"Saved to {p}"

        # 构造执行环境
        exec_data = {
            "logisim_logic": logisim_logic,
            "rs": rs_mod,
            "ll": logisim_logic,
            "model": model_mod,
            "ProjectFacade": ProjectFacade,
            "CircuitEditor": CircuitEditor,
            "load_project": ProjectFacade.load,  # 别名，确保返回 Facade 对象而非 RawProject
            "session": session,
            "proj": session,
            "project": session,
            "editor": editor,
            "ed": editor,
            "RawWire": model_mod.RawWire,
            "component": model_mod.RawComponent,  # 别名
            "Instance": model_mod.RawComponent,  # 别名
            "circuit": model_mod.RawCircuit,  # 别名
            "wire": model_mod.RawWire,  # 别名
            "project_path": abs_in,
            "output_path": abs_out,
            "Union": Any,
            "Optional": Any,
            "Any": Any,
            "Iterable": Any,  # 辅助类型，防止导入错误
            "range": range,
            "len": len,
            "print": print,
            "dict": dict,
            "list": list,
            "set": set,
            "__builtins__": __builtins__,
        }

        def _find_component_plus(name=None, loc=None):
            """增强型组件查找：支持通过 label 或 name 查找。"""
            if name:
                # 优先匹配 label
                for c in raw_circ.components:
                    if (c.label or "").strip() == name:
                        return c
            return rs_mod.find_component(raw_circ, name=name, loc=loc)

        def delete_component(comp):
            """删除指定组件。"""
            return editor.delete_component(comp)

        def get_port_location(ref, port_name):
            """获取组件端口的绝对坐标。支持 label 或 组件对象。"""
            return editor.port_location(ref, port_name)

        exec_data.update(
            {
                "add_instance": add_instance,
                "add_component": add_instance,  # 别名
                "connect": connect,
                "connect_ports_routed": connect,  # 别名
                "find_component": _find_component_plus,
                "delete_component": delete_component,
                "get_port_location": get_port_location,
                "save_circuit": save_circuit,
                "inspect_circuit_context": (lambda p, c: tool_inventory_circuit(p, c)),
            }
        )

        # 注入模型类
        for name, value in model_mod.__dict__.items():
            if name.startswith("Raw") or name == "Point":
                exec_data[name] = value

        output_buffer = StringIO()
        try:
            code_obj = compile(script_code, "design_script.py", "exec")
            with redirect_stdout(output_buffer), redirect_stderr(output_buffer):
                exec(code_obj, exec_data)
        except Exception as inner_e:
            output_buffer_val = output_buffer.getvalue()
            tb_list = traceback.extract_tb(sys.exc_info()[2])
            script_frame = None
            for frame in reversed(tb_list):
                if frame.filename == "design_script.py":
                    script_frame = frame
                    break

            error_line_content = ""
            if script_frame:
                line_no = script_frame.lineno
                lines = script_code.splitlines()
                if 1 <= line_no <= len(lines):
                    error_line_content = lines[line_no - 1]

            sig_info = ""
            if isinstance(inner_e, TypeError):
                # Heuristic to find relevant signatures
                words = set(
                    re.findall(r"\b\w+\b", error_line_content)
                    + re.findall(r"'(\w+)'", str(inner_e))
                )
                for word in words:
                    if word in exec_data:
                        obj = exec_data[word]
                        if callable(obj):
                            try:
                                sig = inspect.signature(obj)
                                sig_info += f"\n>> 建议签名: {word}{sig}"
                            except:
                                pass

            full_tb = traceback.format_exc()
            err_msg = (
                f"脚本执行失败 [{type(inner_e).__name__}: {str(inner_e)}]"
                f"\n>> 错误行号: {script_frame.lineno if script_frame else '?'}"
                f"\n>> 出错代码: {error_line_content.strip()}"
                f"{sig_info}"
                f"\n\n>> 完整回溯:\n{full_tb}"
            )
            if output_buffer_val:
                err_msg += f"\n\n>> 标准输出:\n{output_buffer_val}"

            _log_to_synthesis_log(f"    [Build Error]\n{err_msg}")
            return err_msg

        editor.cleanup_detached_artifacts()
        final_save_path = save_circuit(abs_out)
        _log_to_synthesis_log(f"    [Success] {final_save_path}")
        return f"成功：修改已应用。日志：\n{output_buffer.getvalue()}"
    except Exception as e:
        err_msg = f"执行修改时发生未知错误: {str(e)}\n{traceback.format_exc()}"
        _log_to_synthesis_log(f"    [Fatal Error] {err_msg}")
        return err_msg


async def _tool_run_validation_async(project_path: str, circuit_name: str) -> str:
    """
    (双层验证循环工具) 对生成的电路进行结构验证和行为验证。
    1. 结构层：检查位宽冲突和非法导线。
    2. 行为层：通过 WebSocket 连接 Logisim Headless 后端执行影子仿真测试。
    """
    from src.logisim_logic import load_project, find_width_conflicts
    from src.utils.sim_runner import LogisimEmulator
    from src.utils.config_loader import ConfigManager

    report = {"structural": "passed", "behavioral": "not_started", "details": ""}

    try:
        # 1. 结构验证
        proj = load_project(project_path)
        circ = proj.circuit(circuit_name)
        conflicts = find_width_conflicts(circ, project=proj)
        if conflicts:
            report["structural"] = "failed"
            details = []
            for c in conflicts:
                details.append(
                    f"位宽冲突：{c.kind} 在网络 {c.net_id}，包含宽度 {c.widths()}"
                )
            report["details"] = "\n".join(details)
            return json.dumps(report, ensure_ascii=False)

        # 2. 行为验证 (WebSocket)
        config = ConfigManager.load_config(
            Path(__file__).parents[2] / "config" / "config.toml"
        )
        # 暂时只做连通性测试，复杂的 TTY 测试由 integration_logic 负责
        emu = LogisimEmulator(config, None)
        if await emu.launch_and_initialize(project_path):
            report["behavioral"] = "ready"
            report["details"] = (
                "WebSocket 连接正常，电路已加载。建议下一步运行具体测试向量。"
            )
            emu.close()
        else:
            report["behavioral"] = "failed"
            report["details"] = "无法连接到仿真器后端或电路加载失败。"

        return json.dumps(report, ensure_ascii=False)
    except Exception as e:
        return f"验证工具执行异常: {str(e)}"


def tool_run_validation(project_path: str, circuit_name: str) -> str:
    """
    (双层验证循环工具) 同步包装器，供 Gemini AFC 调用异步验证逻辑。
    """
    return _run_coroutine_in_thread(
        _tool_run_validation_async(project_path, circuit_name)
    )


def tool_write_and_run_python(
    script_code: str,
    working_dir: str = "",
    filename: str = "tool_run.py",
    timeout_seconds: int = 30,
) -> str:
    """
    (通用执行工具) 将 Python 代码写入临时文件并运行，返回 stdout/stderr/退出码。
    适用于公式验证、临时数据处理、快速脚本实验等场景。
    """
    project_root = Path(__file__).parents[2]
    run_dir = project_root / "cache" / "llm_python_runs"
    run_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _sanitize_tool_filename(filename)
    script_path = run_dir / f"{Path(safe_name).stem}_{uuid.uuid4().hex[:8]}.py"
    script_path.write_text(script_code, encoding="utf-8")

    cwd = Path(working_dir).expanduser() if working_dir else project_root
    if not cwd.is_absolute():
        cwd = (project_root / cwd).resolve()

    if not cwd.exists() or not cwd.is_dir():
        return json.dumps(
            {
                "status": "error",
                "message": f"工作目录不存在: {cwd}",
                "script_path": str(script_path),
            },
            ensure_ascii=False,
            indent=2,
        )

    _log_to_synthesis_log(
        f"    [Tool] Running tool_write_and_run_python at {script_path} (cwd={cwd})"
    )

    child_env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    try:
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout_seconds)),
            env=child_env,
        )
        return json.dumps(
            {
                "status": "ok" if completed.returncode == 0 else "error",
                "script_path": str(script_path),
                "working_dir": str(cwd),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
            ensure_ascii=False,
            indent=2,
        )
    except subprocess.TimeoutExpired as exc:
        return json.dumps(
            {
                "status": "error",
                "script_path": str(script_path),
                "working_dir": str(cwd),
                "message": f"执行超时（>{max(1, int(timeout_seconds))} 秒）",
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or "",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "status": "error",
                "script_path": str(script_path),
                "working_dir": str(cwd),
                "message": str(exc),
            },
            ensure_ascii=False,
            indent=2,
        )


def search_web(query: str) -> str:
    """通过搜索引擎查询互联网知识。"""
    base_url = os.getenv("SEARXNG_URL", "http://localhost:8089/")
    try:
        url = f"{base_url}search?q={query}&format=json"
        response = requests.get(url, timeout=10)
        data = response.json()
        results = data.get("results", [])[:5]
        return "\n".join([f"- {r.get('title')}: {r.get('content')}" for r in results])
    except Exception as e:
        # 工具约定：永不抛异常，否则会把单步错误升格为整轮失败。
        # 但必须回传具体错误细节，不能用"搜索失败。"这种无信息字符串糊弄模型。
        return f"搜索失败: {type(e).__name__}: {e} (SEARXNG_URL={base_url})"


# 导出的工具函数列表，供智能体使用
tools_list = [
    tool_inventory_circuit,
    tool_get_geometry,
    tool_check_topology,
    tool_apply_modifications,
    tool_run_validation,
    tool_write_and_run_python,
    search_web,
]
