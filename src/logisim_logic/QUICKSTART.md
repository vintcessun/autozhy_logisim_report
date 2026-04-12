# logisim_logic Quickstart

这份文档只回答一个问题：

如何在 5 分钟内把 `logisim_logic` 用起来。

如果你想看所有接口，去 [API.md](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic/API.md)。
如果你想看更完整的示例，去 [README.md](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic/README.md)。

## 1. 这个模块是干什么的

`logisim_logic` 用来处理 Logisim 经典版 `.circ` 文件。

它不是“直接拼 XML”的小工具，而是分成几层：

1. 保真读写工程。
2. 恢复元件几何、端口和 appearance。
3. 从图面提取逻辑网络。
4. 在原图基础上改属性、挂接新元件、自动布局和布线。

你可以把它理解成：

- 既能像“XML 解析器”那样读写原文件；
- 也能像“逻辑电路工具箱”那样按逻辑关系重建子电路。

## 2. 最小准备

假设你现在在工程根目录，目录里有：

- [logisim_logic](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic)
- 一个或多个 `.circ` 文件
- 你自己的 Python 脚本

最常见的运行方式就是直接在工程根目录下写脚本并运行：

```powershell
python your_script.py
```

脚本里直接：

```python
from logisim_logic import load_project, save_project
```

就可以用了。

## 3. 第一段代码：读一个工程，再保存一份

```python
from pathlib import Path
from logisim_logic import load_project, save_project

project = load_project(Path("16位偶校验传输测试实验.circ"))
print(project.main_circuit_name)
print([circuit.name for circuit in project.circuits])

save_project(project, Path("copy.circ"))
```

这一步的意义是确认三件事：

- 模块能正常 import
- `.circ` 能正常读
- 写回去不会把文件写坏

## 3.5 推荐入口：直接用高级封装

如果你接下来就是要“复制原工程然后微调”，可以直接用 `ProjectFacade`。

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

这个入口适合：

- 从一个原实验复制出新实验
- 选中若干元件后改属性
- 从另一个工程导入已经调好的子电路
- 保存前统一做 padding 归一化

如果 donor 子电路就在另一个 `.circ` 文件里，还可以直接：

```python
session.import_circuit_file("8位扩展海明码传输测试实验.circ", "扩展海明码解码电路")
```

## 4. 第二段代码：看一个子电路里有什么元件

```python
from collections import Counter
from pathlib import Path
from logisim_logic import load_project

project = load_project(Path("8位循环冗余校验码（并行）实验.circ"))
circuit = project.circuit("CRC并行编码电路")

print("元件总数：", len(circuit.components))
print("导线总数：", len(circuit.wires))

counter = Counter(component.name for component in circuit.components)
for name, count in counter.most_common():
    print(name, count)
```

如果你只是想“看看原图结构”，通常从这里开始。

## 5. 第三段代码：看某个元件的真实端口

这是这个模块和普通 XML 处理的关键区别之一。

```python
from pathlib import Path
from logisim_logic import load_project, get_component_geometry

project = load_project(Path("8位循环冗余校验码（并行）实验.circ"))
circuit = project.circuit("随机干扰电路")

target = next(component for component in circuit.components if component.name == "XOR Gate")
geometry = get_component_geometry(target, project=project)

print(target.name, target.loc)
for port in geometry.ports:
    print(port.name, port.offset, port.direction, port.width)
```

什么时候用这个：

- 你想知道元件有哪些输入输出口
- 你要自动挂接一个元件到某个端口
- 你要验证一个 splitter / gate / subcircuit 的位宽

## 6. 第四段代码：把图面提取成逻辑网络

如果你已经不满足于“有哪些元件”，而是想知道“谁和谁连在一起”，就用逻辑提取。

```python
from pathlib import Path
from logisim_logic import load_project, extract_logical_circuit

project = load_project(Path("8位循环冗余校验码（并行）实验.circ"))
circuit = project.circuit("CRC并行编码电路")
logical = extract_logical_circuit(circuit, project=project)

print("实例数：", len(logical.instances))
print("网络数：", len(logical.nets))

for instance in logical.instances[:5]:
    print(instance.id, instance.kind, instance.port_points)

for net in logical.nets[:5]:
    print(net.id, [(ep.instance, ep.port) for ep in net.endpoints], sorted(net.tunnel_labels))
```

什么时候用这个：

- 分析原实验电路到底是什么逻辑结构
- 对比重建前后逻辑是否一致
- 生成你自己的“电路结构摘要”

## 7. 第五段代码：在原图基础上改属性

如果你的目标不是“从零重建”，只是想改 ROM、Pin、Probe、Text，一般优先用选择器和模板工具。

```python
from pathlib import Path
from logisim_logic import load_project, save_project, CircuitTemplate
from logisim_logic.selection import select_component

project = load_project(Path("汉字编码实验.circ"))
circuit = project.circuit("汉字编码实验")

selectors = {
    "rom": select_component("ROM"),
    "probe": select_component("Probe", contains={"label": "发送数据"}),
}

template = CircuitTemplate(base_circuit=circuit, circuit=circuit, selectors=selectors)
template.set_probe("probe", label="发送数据(16位)", radix="16")

save_project(project, Path("汉字编码实验-修改后.circ"))
```

这类场景最常见：

- 改 Probe 标签、进制、位宽
- 改 Pin 位宽
- 改 Text 说明文字
- 改 ROM 内容

## 8. 第六段代码：按逻辑关系新建一个小电路

如果你要“用户只描述逻辑，不手写坐标”，优先用 `LogicCircuitBuilder`。

```python
from logisim_logic import LogicCircuitBuilder

builder = LogicCircuitBuilder("demo")
builder.add_instance("in", "Pin", {"facing": "east", "output": "false", "width": "8"}, lib="0")
builder.add_instance("out", "Pin", {"facing": "west", "output": "true", "width": "8"}, lib="0")
builder.connect("in.io", "out.io", label="DATA")

circuit = builder.build()
print(circuit.name)
print(len(circuit.components), len(circuit.wires))
```

这个 builder 负责：

- 自动摆放元件
- 自动布线
- 必要时处理较复杂的连线展开

## 9. 第七段代码：把新子电路塞回原工程

```python
from pathlib import Path
from logisim_logic import load_project, save_project, LogicCircuitBuilder
from logisim_logic.project_tools import replace_circuit

project = load_project(Path("test.circ"))

builder = LogicCircuitBuilder("新子电路")
builder.add_instance("in", "Pin", {"facing": "east", "output": "false", "width": "1"}, lib="0")
builder.add_instance("out", "Pin", {"facing": "west", "output": "true", "width": "1"}, lib="0")
builder.connect("in.io", "out.io", label="SIG")

new_circuit = builder.build()
replace_circuit(project, new_circuit)
save_project(project, Path("test_modified.circ"))
```

这是“重建一个子电路，再写回原工程”的最短路径。

## 10. 命令行怎么用

这个模块还带了一个简单 CLI。

### 导出整个工程 JSON

```powershell
python -m logisim_logic dump-project .\16位偶校验传输测试实验.circ
```

### 导出某个子电路的逻辑网表 JSON

```powershell
python -m logisim_logic dump-logic .\8位循环冗余校验码（并行）实验.circ "CRC并行编码电路"
```

### 把一段汉字转成 Logisim ROM 文本

```powershell
python -m logisim_logic gb2312-rom "厦门大学信息学院欢迎您！"
```

## 11. 什么时候该用哪个接口

最简单的判断方式：

- 只想读写 `.circ`：
  用 `load_project()` / `save_project()`

- 只想分析原图：
  用 `get_component_geometry()`、`build_wire_graph()`、`extract_logical_circuit()`

- 只想在原图基础上改属性：
  用 `select_component()` + `CircuitTemplate`

- 想自动生成一个新逻辑子电路：
  用 `LogicCircuitBuilder`

- 想在原图某个端口边上挂接一个新模块：
  用 `rebuild_support.py` 里的 `attach_*` 系列

- 想做实验级重建脚本：
  组合 `project_tools.py`、`selection.py`、`template_tools.py`、`rebuild_support.py`

## 12. 建议工作流

推荐按这个顺序工作：

1. `load_project()` 打开原工程
2. `project.circuit(name)` 选中目标子电路
3. 用 `circuit.components` + `get_component_geometry()` 先理解原图
4. 如果要分析逻辑，再跑 `extract_logical_circuit()`
5. 如果只是改属性，用 `CircuitTemplate`
6. 如果要重建，用 `LogicCircuitBuilder`
7. `replace_circuit()` 写回工程
8. `save_project()` 保存

## 13. 常见坑

- 这个模块面向 Logisim 经典版，不是 Logisim Evolution。
- `RawCircuit` 是原始保真结构，适合精细修改；`LogicalCircuit` 是逻辑抽象，适合分析，不适合直接保存。
- 想保留原 appearance 时，不要手写 `<appear>`，优先复用 `rebuild_support.py` 里的工具。
- 做重建脚本时，先保逻辑正确，再看布局美观；这两个层次不要混着写。

## 14. 下一步看什么

如果你已经能跑通上面的代码，下一步建议：

1. 看 [README.md](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic/README.md) 里的完整示例。
2. 看 [API.md](C:/Users/xqy2006/Downloads/第一次实验电路/logisim_logic/API.md) 查具体接口。
3. 看工程里的 [rebuild_experiments.py](C:/Users/xqy2006/Downloads/第一次实验电路/rebuild_experiments.py) 学“实验级重建脚本”怎么写。
