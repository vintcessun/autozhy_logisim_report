# logisim_logic

`logisim_logic` 是一个面向 Logisim 经典版 `.circ` 文件的 Python 模块。

它的目标不是直接拼 XML，而是把工作拆成几层：

1. 保真读写层：完整读取和写回 Logisim 工程。
2. 几何层：恢复元件边界、端口、朝向、appearance 信息。
3. 逻辑层：把元件和导线提成实例、端口、网络。
4. 重建层：在原图基础上自动改属性、挂接新元件、布局、布线。
5. 高级封装层：直接按“工程 / 电路 / 选择器 / 微调动作”写脚本。

完整接口手册见 [API.md](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic/API.md)。
第一次上手建议先看 [QUICKSTART.md](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic/QUICKSTART.md)。

## 快速开始

```python
from pathlib import Path
from logisim_logic import load_project, save_project

project = load_project(Path("16位扩展海明码传输测试实验.circ"))
print(project.main_circuit_name)
save_project(project, Path("copy.circ"))
```

## 什么时候用什么

- 只想改原电路里的 Probe、Pin、ROM、Text：优先用 `selection.py` + `CircuitTemplate`
- 想把“加载工程、复制子电路、导入子电路、保存”收成更短的脚本：优先用 `ProjectFacade` + `CircuitEditor`
- 想从逻辑关系重建一个编码器/解码器：优先用 `LogicCircuitBuilder`
- 想在原图某个端口旁边自动挂一个新模块：优先用 `rebuild_support.py` 里的 `attach_*`
- 想看导线连通关系或逻辑网络：用 `build_wire_graph()` / `extract_logical_circuit()`

## 示例 1：读取电路里有哪些元器件

这个例子会：

- 打开一个工程
- 列出所有子电路名字
- 统计某个子电路里每种元件各有多少个
- 打印带标签的元件，方便快速认识原图结构

```python
from collections import Counter
from pathlib import Path
from logisim_logic import load_project

project = load_project(Path("8位循环冗余校验码（并行）实验.circ"))

print("所有子电路：")
for circuit in project.circuits:
    print(" ", circuit.name)

circuit = project.circuit("CRC码传输测试实验2")
print("\n当前电路：", circuit.name)

kind_counter = Counter(comp.name for comp in circuit.components)
print("\n元件统计：")
for kind, count in kind_counter.most_common():
    print(f"  {kind}: {count}")

print("\n带 label 的元件：")
for comp in circuit.components:
    label = comp.get("label", "") or ""
    if label:
        print(f"  {comp.name:18s} loc={comp.loc} label={label}")
```

如果你想更细一点，也可以直接看元件属性：

```python
for comp in circuit.components[:5]:
    print(comp.name, comp.loc, comp.attr_map())
```

## 示例 2：看一个元件有哪些端口、端口宽度是多少

```python
from pathlib import Path
from logisim_logic import load_project, get_component_geometry

project = load_project(Path("8位循环冗余校验码（并行）实验.circ"))
circuit = project.circuit("随机干扰电路")

target = next(comp for comp in circuit.components if comp.name == "Bit Extender")
geometry = get_component_geometry(target, project=project)

print(target.name, target.loc)
for port in geometry.ports:
    print(port.name, "offset=", port.offset, "width=", port.width, "direction=", port.direction)
```

这适合：

- 判断一个元件的真实输入输出端口名
- 给 `attach_subcircuit_to_port(..., attached_port_name=...)` 这类接口提供正确端口名

## 示例 3：怎么看电路的逻辑结构

`extract_logical_circuit()` 会把原始元件和导线提成“实例 + 端口 + 网络”。

```python
from pathlib import Path
from logisim_logic import load_project, extract_logical_circuit

project = load_project(Path("8位循环冗余校验码（并行）实验.circ"))
circuit = project.circuit("CRC并行编码电路")
logical = extract_logical_circuit(circuit, project=project)

print("逻辑电路名：", logical.name)
print("实例数：", len(logical.instances))
print("网络数：", len(logical.nets))

print("\n前 5 个实例：")
for instance in logical.instances[:5]:
    print(instance.id, instance.kind, instance.loc)
    print("  ports =", instance.port_points)

print("\n前 5 条网络：")
for net in logical.nets[:5]:
    print(net.id)
    print("  endpoints =", [(ep.instance, ep.port) for ep in net.endpoints])
    print("  tunnel_labels =", sorted(net.tunnel_labels))
```

你可以把它理解成：

- `instances`：电路里有哪些逻辑元件
- `port_points`：每个元件的端口落在什么位置
- `nets`：哪些端口实际上属于同一根逻辑网络
- `tunnel_labels`：这条网络上有没有 tunnel 语义名

## 示例 4：看导线连通图，而不是逻辑抽象

如果你只是想知道“哪几个点在电气上连通”，用 `build_wire_graph()` 更直接。

```python
from pathlib import Path
from logisim_logic import load_project, build_wire_graph

project = load_project(Path("8位循环冗余校验码（并行）实验.circ"))
circuit = project.circuit("CRC并行编码电路")
graph = build_wire_graph(circuit)

print("网络数：", len(graph.nets))

for net in list(graph.nets.values())[:3]:
    print(net.id, sorted(net.points)[:10])
```

这更适合：

- 检查某根线有没有断
- 检查某个点是不是交叉但未连接
- 调试布局/布线器

## 示例 5：在原图基础上改属性

```python
from pathlib import Path
from logisim_logic import CircuitTemplate, load_project
from logisim_logic.selection import select_component

project = load_project(Path("汉字编码实验.circ"))
circuit = project.circuit("汉字编码实验")

selectors = {
    "rom": select_component("ROM"),
    "input_pin": select_component("Pin", label="地址", output="false"),
}

template = CircuitTemplate(base_circuit=circuit, circuit=circuit, selectors=selectors)
template.set_width("input_pin", 8)
template.set_rom_words("rom", addr_width=8, data_width=16, words=[0xCFC3, 0xC3C5])
```

## 示例 5.5：用高级封装改原工程

如果你不想自己管理 `clone_project()`、`replace_circuit()`、`save_project()` 这些工程级动作，可以直接从 `ProjectFacade` 开始。

```python
from pathlib import Path
from logisim_logic import ProjectFacade, select_component

session = ProjectFacade.load(Path("汉字编码实验.circ"))
editor = session.edit_circuit(
    "汉字编码实验",
    selectors={"rom": select_component("ROM")},
)
editor.set_rom_words("rom", addr_width=8, data_width=16, words=[0xCFC3, 0xC3C5])
session.save(Path("汉字编码实验-修改后.circ"))
```

## 示例 6：从逻辑关系重建一个子电路

```python
from logisim_logic import LogicCircuitBuilder

builder = LogicCircuitBuilder("demo")
builder.add_instance("in", "Pin", {"facing": "east", "output": "false", "width": "8"}, lib="0")
builder.add_instance("out", "Pin", {"facing": "west", "output": "true", "width": "8"}, lib="0")
builder.connect("in.io", "out.io", label="DATA")

circuit = builder.build()
```

## 推荐写法

- 先用 `load_project()` 和 `project.circuit(name)` 把目标子电路拿出来
- 如果要研究原图，优先看：
  - `circuit.components`
  - `get_component_geometry()`
  - `build_wire_graph()`
  - `extract_logical_circuit()`
- 如果要改原图，优先用 `select_component()` / `CircuitTemplate`
- 如果要重建逻辑，优先用 `LogicCircuitBuilder`
- 如果要挂接新元件，优先用 `attach_*`，并显式指定 `attached_port_name`

## 说明

- 这个模块优先服务 Logisim 经典版，不是 Logisim Evolution。
- 自动布局和自动布线是模块自己的实现。
- 一些很偏脚本辅助的函数放在 `rebuild_support.py`，完整说明见 [API.md](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic/API.md)。
