# 用 `logisim_logic` 从 0 开始完成任意 Logisim 电路修改任务

这份文档不依赖仓库里现有的任务脚本。

假设读者手里只有：

1. 一个或多个待修改的 `.circ` 文件。
2. 一份任务说明。
3. `logisim_logic` 库。
4. 可选：一个能运行 TTY 的 Logisim JAR。

目标是回答三个问题：

1. 如何从 0 开始分析一个电路修改任务。
2. 如何用 `logisim_logic` 在原电路基础上设计和实现修改。
3. 如何自己编写 `validate` 脚本，验证行为和结构。


## 0. 先把环境约束钉死

开始之前，先固定下面几条纪律。

### 0.1 仿真器只用 `generic`

如果你的库是按传统 Logisim / generic 兼容层写的，行为验证和可加载性验证都优先使用 `logisim-generic-2.7.1.jar`。

不要默认拿 `logisim-evolution` 代跑，因为它经常会带来下面几类假问题：

1. 组件库编号不同，导致 `component 'Negator' missing from library '2'` 这类错误。
2. 同名组件的属性集合不同。
3. 某些 `Splitter`、`Tunnel`、显示器件的兼容性不同。
4. 你以为是电路错了，实际上只是 JAR 不兼容。

如果任务没有明确要求别的版本，优先把“生成、加载、验证”三件事统一在 `generic` 上。


### 0.2 保存后先检查 `<project source="...">`

`.circ` 的根节点里有 `source` 字段。

如果这个字段被错误写成了路径后缀、可执行文件名后缀或者其他奇怪字符串，Logisim 可能直接报：

1. `XML formatting error`
2. `Invalid version suffix format`

最稳妥的做法是：

1. 从原始工程继承一个稳定的版本号。
2. 或者显式归一成一个你确认可加载的值，例如 `2.15.0`。

最小修正示例：

```python
import re
from pathlib import Path


def normalize_project_source(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    updated = re.sub(r'(<project\\s+source=\")([^\"]+?)(\")', r"\\g<1>2.15.0\\3", text, count=1)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
```

注意，这一步属于“保存后最终清理”，不要等到最后 GUI 打不开才想起来查。


### 0.3 每次大改后都要重新加载一次

不要只依赖“脚本运行没报错”。

每次完成一轮大修改后，至少做一次：

1. `save(...)`
2. `load_project(...)`
3. 检查主电路名、子电路引用、组件库引用是否正常

这一步能很早抓住：

1. 丢失子电路引用
2. 保存出的 XML 非法
3. 组件库编号写错
4. 属性值被写成不被 Logisim 接受的格式


## 1. 先建立正确心智模型

`logisim_logic` 不是“自动画图工具”，而是“把 Logisim 电路当作可编程结构来编辑”的库。

你真正操作的是：

1. 工程 `RawProject`
2. 子电路 `RawCircuit`
3. 组件 `RawComponent`
4. 导线 `RawWire`
5. 组件几何、端口、逻辑网、布局分析结果

做题时，你不应该把工作理解成“改 XML 文本”，而应该理解成：

1. 读取工程。
2. 找到要修改的子电路。
3. 删除不再需要的组件和导线。
4. 修改保留组件的属性、位宽、标签、分线器映射。
5. 增加新组件和新连线。
6. 清理修改后留下的残线、悬空 Tunnel、错误连接。
7. 验证功能和结构。


## 2. 库的大致分层

当你需要知道“该去哪里找功能”时，可以按下面理解：

1. `logisim_logic/xml_io.py`、`model.py`
   负责读取/保存 `.circ`，定义原始数据结构。

2. `logisim_logic/high_level.py`
   负责高层编辑接口，例如 `ProjectFacade`、`CircuitEditor`。

3. `logisim_logic/rebuild_support.py`
   负责常用辅助编辑，例如加组件、加导线、改 Splitter。

4. `logisim_logic/geometry.py`
   负责算组件的边界和端口位置。

5. `logisim_logic/logical.py`、`graph.py`
   负责把图上的导线和 Tunnel 解析成逻辑网。

6. `logisim_logic/layout.py`
   负责重叠检测、布局计算。

如果是“我要找组件、改属性、删线”，先看 `high_level.py`。

如果是“我要改 Splitter 映射、加正交折线”，先看 `rebuild_support.py`。

如果是“为什么这两根线逻辑上短路了”，先看 `logical.py` 和 `graph.py`。


## 3. 从 0 开始做任务的总流程

推荐固定按下面顺序做。

### 阶段 A：分析任务

先把题目翻译成 5 个问题：

1. 输入工程文件是什么。
2. 输出工程文件应该叫什么。
3. 哪几个子电路需要改。
4. 每个子电路的变化类型是什么。
5. 需要验证哪些行为。

“变化类型”通常只有几类：

1. 只改显示和标签。
2. 缩位宽，例如 16 位改 8 位。
3. 改编码规则，例如偶校验改奇校验。
4. 改主电路封装，不改核心逻辑。
5. 删除一半高位通路，再重接保留通路。
6. 插入少量新组件，例如 `NOT Gate`、`Constant`、`Multiplexer`。

如果任务很复杂，不要一开始就写“最终脚本”，先在纸上列出：

1. 主电路要改什么。
2. 编码器要改什么。
3. 解码器要改什么。
4. 哪些模块只改位宽，哪些模块要重接线。


### 阶段 B：盘点原工程

先看原工程，不要先猜。

推荐做 3 件事：

1. 用 Logisim GUI 打开原始电路，肉眼看模块关系和布局密集区域。
2. 用脚本列出所有子电路名。
3. 用脚本查看目标子电路中关键组件的坐标、属性、位宽、标签。

最小盘点脚本示例：

```python
from logisim_logic import load_project

project = load_project("输入工程.circ")

print(project.circuit_names())

circuit = project.circuit("目标子电路")
for comp in circuit.components:
    if comp.name in {"Pin", "Tunnel", "Splitter", "XOR Gate", "Multiplexer"}:
        print(comp.name, comp.loc, comp.attr_map())
```

只要你准备按坐标修改，就一定要先把坐标准确盘出来。


### 阶段 C：设计修改方案

这一步不要直接写代码，先回答：

1. 哪些原组件可以保留。
2. 哪些原组件必须删除。
3. 哪些原组件虽然保留位置，但属性必须改。
4. 新增组件要放在哪里。
5. 旧线删掉后，新线如何接回去。

默认策略应当是：

1. 优先“在原电路基础上修改”
2. 只删除确实不再需要的组件和导线
3. 只在原结构完全不适合任务时，才考虑大规模重建

原因是原电路里通常已经包含：

1. 合适的输入输出展示区
2. 已经调好的封装接口
3. 已经对齐的布局
4. 老师或出题人默认希望保留的实验结构

全重建虽然有时更快，但也更容易：

1. 覆盖掉展示区域
2. 丢掉原有封装约束
3. 让验证脚本和 GUI 使用体验一起退化

设计方案时，优先选下面三种修改方式之一。

#### 方式 1：只改属性

适合：

1. 改 `Pin` / `Probe` / `Comparator` 位宽
2. 改文字
3. 改标签
4. 改门的输入数、位宽

#### 方式 2：删掉高位分支，再重接保留分支

适合：

1. 16 位改 8 位
2. 32 位改 13 位
3. 原电路一半高位逻辑不再使用

这类任务的关键不是“加新逻辑”，而是：

1. 高位组件删干净
2. 高位相关 Tunnel 删干净
3. 高位相关导线删干净
4. 保留下来的 Splitter 和 Gate 参数改完整

#### 方式 3：保留大部分结构，中间插一个小逻辑

适合：

1. 偶校验改奇校验
2. 某一路逻辑反相
3. 补一个常量选择支路

这类任务最容易出现的问题是：

1. 元件摆位碰撞
2. 旧导线没删干净
3. 新导线绕路导致意外短路


### 阶段 D：实现修改

推荐每个任务写一个独立脚本，结构保持固定。

最小骨架如下：

```python
from pathlib import Path

from logisim_logic import ProjectFacade

ROOT = Path(__file__).resolve().parent


def main() -> None:
    session = ProjectFacade.load(ROOT / "输入工程.circ").clone()

    # 1. 重命名电路
    session.rename_circuit("原主电路", "新主电路")
    session.set_main("新主电路")

    # 2. 修改某个子电路
    editor = session.edit_circuit("目标子电路")
    # 在这里做删改增
    editor.cleanup_detached_artifacts()
    editor.preserve_appearance_from_base()

    # 3. 保存
    session.normalize_root_padding()
    session.save(ROOT / "输出工程.circ")


if __name__ == "__main__":
    main()
```

固定流程有两个好处：

1. 调试时你知道每一层改完后该检查什么。
2. 后面写 `validate` 脚本时，你知道输入文件和主电路名应该是什么。


## 4. 实现时最常用的 API

### 4.1 工程入口：`ProjectFacade`

最常用的是：

1. `ProjectFacade.load(path)`
2. `clone()`
3. `circuit_names()`
4. `circuit(name)`
5. `rename_circuit(old, new)`
6. `set_main(name)`
7. `edit_circuit(name)`
8. `save(path)`


### 4.2 子电路编辑：`CircuitEditor`

最常用的是：

1. `component_at(kind=..., loc=(x, y))`
   用坐标精确找组件。

2. `remove_components([...])`
   删除一组组件。

3. `remove_where(...)`
   按条件删除组件。

4. `port_point(component, "out")`
   算某个端口在图上的坐标。

5. `connect_points(...)`
   连导线。

6. `connect_ports(...)`
   从组件端口连到组件端口。

7. `replace_text_exact(...)`
8. `update_text_contains(...)`
9. `cleanup_detached_artifacts()`
10. `preserve_appearance_from_base()`


### 4.3 常用低层辅助：`rebuild_support`

最常用的是：

1. `add_component(...)`
2. `add_polyline(...)`
3. `connect_points_routed(...)`
4. `connect_ports_routed(...)`
5. `add_tunnel(...)`
6. `add_tunnel_to_port(...)`
7. `add_tunnel_on_port(...)`
8. `detunnelize_selected_tunnels(...)`
9. `set_splitter_single_bit(...)`
10. `set_splitter_two_way(...)`
11. `set_splitter_extract(...)`
12. `remove_components_at_locs(...)`
13. `rename_tunnel_labels(...)`

如果你要改 Splitter，不要自己手改一堆 `bit0`、`bit1`、`bit2`。
优先用这些 helper。

如果你要接 `Tunnel`，优先用 `add_tunnel(...)`、`add_tunnel_to_port(...)`、`add_tunnel_on_port(...)`，不要直接 `add_component("Tunnel", ...)` 再自己瞎拉线。

如果一条连接并不需要语义名，只是想把两个端口直接接起来，优先用 `connect_points_routed(...)` 或 `connect_ports_routed(...)`，不要为了省事硬塞一个中间 Tunnel。

这两个 routed API 的默认语义应该是：

1. 避开元件和无关端口
2. 允许普通导线十字交叉
3. 不把“交叉”误判成“连接”
4. 只把端点接触或显式分叉当作真正并网

如果你想减少自己额外制造的中间 Tunnel，推荐固定按下面顺序判断：

1. 这条连接只是为了把两个点接起来，没有“语义名”需求
   直接用 `connect_points_routed(...)` 或 `connect_ports_routed(...)`
2. 这条连接需要跨较远区域，而且你确实希望在图上保留一个稳定信号名
   才考虑 `Tunnel`
3. 这条连接只是脚本内部为了省坐标计算临时搭出来的跳线
   不要建 Tunnel，优先走 routed API
4. 这条连接以后可能被验证脚本、GUI 阅读或人工调试依赖
   才给它一个有意义的 Tunnel 标签

一个简单判断法是：

如果去掉这个 Tunnel 之后，别人仍然能直接看懂它只是“线怎么走”，那它大概率不该是 Tunnel。

原因是一个成熟的库实现通常应该替你处理：

1. `facing` 方向
2. 端口外引短线
3. 中心端口和边缘端口的区别
4. 同一侧多个 Tunnel 的槽位分配
5. 失败时的 fallback


## 5. 如何读懂一个子电路

只看 GUI 往往不够。

推荐同时做下面 4 类观察。

### 5.1 看组件摘要

```python
from collections import Counter
from logisim_logic import load_project

project = load_project("输入工程.circ")
circuit = project.circuit("目标子电路")

print(Counter(comp.name for comp in circuit.components))
print("wires:", len(circuit.wires))
```

这个能帮你快速知道：

1. 是不是一个“Splitter 密集型”电路
2. 是不是一个“Tunnel 密集型”电路
3. 是不是一个“Mux/Decoder 密集型”电路


### 5.2 看关键组件属性

```python
for comp in circuit.components:
    if comp.name in {"Pin", "Splitter", "Tunnel", "Multiplexer", "Decoder"}:
        print(comp.name, comp.loc, comp.attr_map())
```

尤其要看：

1. `width`
2. `inputs`
3. `select`
4. `enable`
5. `label`
6. `incoming`
7. `fanout`


### 5.3 看端口几何

如果你不知道导线应该接哪儿，就查端口位置：

```python
from logisim_logic import get_component_geometry

comp = circuit.components[0]
geom = get_component_geometry(comp, project=project)
for port in geom.ports:
    print(port.name, port.offset, port.direction, port.width)
```

这一步特别重要，因为很多“删线不干净”和“新线短路”都和端口点判断错误有关。


### 5.4 看逻辑网，而不是只看图

如果行为不对，直接看逻辑网：

```python
from logisim_logic import extract_logical_circuit

logic = extract_logical_circuit(circuit, project=project)
for net in logic.nets:
    if net.tunnel_labels:
        print(net.id, sorted(net.tunnel_labels))
        for ep in net.endpoints:
            print(" ", ep.instance, ep.port)
```

这能帮助你发现：

1. 哪些 Tunnel 真连到了同一张网
2. 哪些端口实际上没连上
3. 哪两张不该相连的网被短接了


## 6. 宽度修改任务的通用清单

只要任务涉及“位宽变化”，都按这张清单过一遍。

### 输入/输出层

1. `Pin.width`
2. `Probe.width`
3. `Comparator.width`
4. `Constant.width`
5. `ROM` / `RAM` / `Adder` / `Subtractor` / `Multiplier` 等算术器件宽度

### 连通层

1. `Tunnel.width`
2. Gate 的 `width`
3. `Multiplexer.width`
4. `Decoder.select`
5. `Splitter.incoming`
6. `Splitter.fanout`
7. `Splitter.bitN`

### 结构层

1. 超出新位宽的高位组件删掉
2. 高位相关 Tunnel 删掉
3. 高位相关导线删掉
4. 新位宽下保留支路重新连线
5. 修改后调用 `cleanup_detached_artifacts()`

最常见的坑是：

1. `Pin` 改成 8 位了，但 `Tunnel` 还是 16 位
2. `XOR Gate` 改成 8 位了，但后面的 `AND Gate` 还是 16 位
3. `Splitter.incoming` 改了，但旧 `bitN` 没清掉
4. 高位组件删了，但高位导线和 Tunnel 还留着


## 7. 如何安全地删组件、删线、重接线

### 7.1 删除组件时，不要只想着“组件没了”

真正要考虑的是：

1. 它的端口连接点还会不会留着导线
2. 它的关联 Tunnel 还会不会悬空
3. 周围旧导线会不会和新导线短接

当前推荐流程是：

```python
editor.remove_components([...])
editor.cleanup_detached_artifacts()
```

如果你删的是一整段高位分支，通常还需要手动删一批条件匹配的 Tunnel。


### 7.2 连线时，优先正交折线

不要手工生成斜线。

优先用：

```python
editor.connect_points((100, 100), (140, 100), (140, 160))
```

或者：

```python
editor.connect_ports(src, "out", dst, "in0", via=[(140, 100), (140, 160)])
```

如果你要自己加 `add_polyline()`，也要确保路径是正交的。


### 7.3 用 Tunnel 桥接时，不要把 Tunnel 压在组件本体上

Tunnel 很适合做“远距离逻辑桥接”，但摆位要谨慎。

建议：

1. 优先使用库里的 `add_tunnel(...)` / `add_tunnel_to_port(...)` / `add_tunnel_on_port(...)`
2. Tunnel 放在端口附近，但不要放在端口正中心的组件边界里
3. Tunnel 和组件之间用一小段短线连接
4. `facing` 要和端口朝向一致，不要出现“端口朝下、Tunnel 却朝上盖住元件”的情况
5. 不要让多个 Tunnel 挤在同一个密集区域

如果你的库对 Tunnel 碰撞体积估算还不成熟，那么：

1. “非 Tunnel 组件重叠”应作为硬错误
2. “Tunnel 相关重叠”先当可疑项，再结合 GUI 复核

更具体地说，成熟的 Tunnel API 至少应该做到：

1. 普通边缘端口优先“紧贴悬挂”，先尝试最短外引短线
2. 同侧多个 Tunnel 需要有槽位分配，不能永远压在同一个点上
3. 中心端口不能套用普通边缘端口逻辑
4. 删组件或改位宽后，相关 Tunnel 和短线也要能被后续清理逻辑处理

如果你准备把部分 Tunnel 还原成真实导线，优先用 `detunnelize_selected_tunnels(...)`，不要直接对整张图无差别去 Tunnel。

推荐规则是：

1. 先记录模板里原本就存在的 Tunnel 标签
2. 只清理模板里不存在的内部中间标签
3. 如果新增 Tunnel 与模板 Tunnel 同名，说明它在接模板语义接口，这一整组都保留
4. 真要全图批量去 Tunnel，再考虑 `detunnelize_circuit(...)`

更进一步地说，模板 Tunnel 和你后加的 Tunnel 应当严格区分：

1. 模板里原本就有的 Tunnel
   通常属于输入展示、输出展示、封装接口或老师预设的语义标记，默认保留
2. 你为了接内部组件临时加的 Tunnel
   只有当它确实承担语义命名作用时才保留
3. 你后加的 Tunnel 如果与模板 Tunnel 同名
   这通常意味着你在把内部逻辑接到模板语义接口上，这一整组都不能删
4. 如果你本来只是想做内部跳线，却误用了模板同名标签
   应该优先改脚本设计，减少这种命名碰撞，而不是事后硬删一半

推荐在脚本开头先记录模板 Tunnel 标签：

```python
def tunnel_labels(circuit):
    return {
        (comp.get("label", "") or "")
        for comp in circuit.components
        if comp.name == "Tunnel"
    }


original_tunnel_labels = tunnel_labels(circuit)
```

等内部逻辑加完以后，再只尝试清理“模板里不存在的内部标签”：

```python
from logisim_logic.rebuild_support import detunnelize_selected_tunnels


detunnelize_selected_tunnels(
    circuit,
    remove_predicate=lambda tunnel: (tunnel.get("label", "") or "") not in original_tunnel_labels,
    keep_labels=set(original_tunnel_labels),
    project=project,
    passes=4,
    check_widths=True,
)
```

这个模式的核心不是“尽量多删 Tunnel”，而是：

1. 模板语义接口不丢
2. 纯内部中间 Tunnel 尽量少
3. 清理动作可控且可回滚
4. 清理后必须重新跑结构验证和功能验证

第二次实验里一个很重要的经验是：

并不是所有内部 Tunnel 都适合清掉。

例如先行进位类电路里的组间中间信号，如果你把某些内部 Tunnel 批量改成真实导线，功能可能会悄悄变化，即使没有位宽冲突、GUI 里也不一定立刻报错。

因此推荐策略不是“看到内部 Tunnel 就删”，而是：

1. 先删最明显的无语义临时跳线
2. 每删一小类标签就重新验证
3. 一旦某类标签的清理引入功能回归，就保留该类标签
4. 以行为正确优先于图面绝对整洁

如果你发现自己在每个任务脚本里都要手写一份 Tunnel 摆位补丁，这通常说明应该回库层修 API。


#### 7.3.1 建议先写一层“任务内 helper”

虽然库已经提供了高层接口，但真要完成一整批实验题，仍然强烈建议先在任务脚本里写一层很薄的 helper，把“常用组件的默认属性”和“generic 的库编号”固定下来。  
这样做的目的不是绕过库，而是减少重复样板、降低写错属性的概率。

第二次实验里高频出现的 helper 形态大致如下：

```python
from logisim_logic import component


def add_component(circuit, name, loc, attrs=None, *, lib=None):
    comp = component(name, loc, {k: str(v) for k, v in (attrs or {}).items()}, lib=lib)
    circuit.components.append(comp)
    return comp


def add_subcircuit(circuit, name, loc):
    return add_component(
        circuit,
        name,
        loc,
        {
            "facing": "east",
            "label": "",
            "labelloc": "north",
            "labelfont": "Dialog plain 12",
            "labelcolor": "#000000",
        },
        lib=None,
    )


def add_splitter(circuit, loc, *, facing, incoming, groups, appear="center"):
    attrs = {"facing": facing, "fanout": len(set(groups)), "incoming": incoming, "appear": appear}
    for i, g in enumerate(groups):
        attrs[f"bit{i}"] = g
    return add_component(circuit, "Splitter", loc, attrs, lib="0")
```

在 `generic` 兼容层里，第二次实验实际用到的常见库编号模式可以先按下面记：

1. `lib="0"`：`Pin`、`Constant`、`Splitter`、`Bit Extender`、`Tunnel`、`Clock`
2. `lib="1"`：`AND Gate`、`XOR Gate`、`NOT Gate`
3. `lib="2"`：`Multiplexer`
4. `lib="3"`：`Negator`、`Comparator`
5. `lib=None`：子电路实例

这张表不是“永远不变的真理”，但足够覆盖绝大多数 generic 实验工程。  
如果某个器件加载时报 `missing from library`，优先先查：

1. 是否误用了 `evolution`
2. helper 里给错了 `lib`
3. 原工程同类器件的 `lib` 是多少

另外，凡是依赖端口几何、端口宽度或自动路由的 helper，建议统一把 `project` 显式传进去。  
例如：

1. `add_tunnel_on_port(..., project=project)`
2. `connect_ports_routed(..., project=project)`
3. `get_component_geometry(comp, project=project)`

不要偷懒省掉这个参数。  
在多子电路、子电路封装较深或端口几何依赖库信息的任务里，省掉 `project` 很容易导致：

1. 端口点算错
2. 宽度识别错
3. 自动导线绕错目标端口


#### 7.3.2 模板电路编辑时，除了逻辑本体，还要同步处理 4 类对象

很多第一次写脚本的人会只盯着“元件和导线”，但第二次实验说明，真正稳定的构建脚本通常还会同步处理下面 4 类对象：

1. 电路名
2. 模板文字
3. 子电路外观 `appearance`
4. 模板 Tunnel 集合

##### 电路名和模板文字

如果输出文件要求新名字，通常不仅要改文件名，还要：

1. `rename_circuit(old, new)`
2. `set_main(new)`
3. 替换电路内部标题文字
4. 删除“请同学们在此处设计电路！”之类占位提示

常用模式：

```python
def remove_texts(circuit, *texts):
    targets = set(texts)
    circuit.components = [
        comp for comp in circuit.components
        if not (comp.name == "Text" and (comp.get("text", "") or "") in targets)
    ]


def replace_text_contains(circuit, old, new):
    for comp in circuit.components:
        if comp.name != "Text":
            continue
        text = comp.get("text", "") or ""
        if old in text:
            comp.set("text", text.replace(old, new), as_text=False)
```

##### 需要补封装引脚时，要同步改 `appearance`

如果原子电路已经封装好，但题目要求你把内部 `Button` / `LED` 改成真正的 `RST` / `CLK` / `END` 引脚，仅仅在子电路本体里新增 `Pin` 还不够。  
还必须同步修改 `circuit.appearances` 里的 `circ-port`，否则：

1. GUI 里封装外观不会出现新端口
2. 外部子电路实例可能看不到或接不到这些端口
3. 你会误以为“库不支持新增 pin”，其实只是外观没同步

稳定模式是：

1. 先删掉旧的 `Button` / `LED`
2. 新增真正的 `Pin`
3. 遍历 `circuit.appearances`
4. 删除与旧交互件重叠的 `circ-port`
5. 手工追加新的 `circ-port`

如果题目明确要求“不要改原封装接口”，就不要这样做。  
但只要任务本身要求把时序控制暴露给外部测试夹具，这就是必须掌握的模式。

##### 记录模板 Tunnel，内部 Tunnel 只清自己的

第二次实验已经验证，这一步是增量修改脚本能否长期稳定的关键做法之一。

脚本开始时先记录模板里原本存在的 Tunnel 标签：

```python
def tunnel_labels(circuit):
    return {
        (comp.get("label", "") or "")
        for comp in circuit.components
        if comp.name == "Tunnel"
    }


original_tunnel_labels = tunnel_labels(circuit)
```

内部逻辑加完以后，只清理“模板里本来没有、且只是内部跳线”的 Tunnel：

```python
from logisim_logic.rebuild_support import detunnelize_selected_tunnels


detunnelize_selected_tunnels(
    circuit,
    remove_predicate=lambda tunnel: (tunnel.get("label", "") or "") not in original_tunnel_labels,
    keep_labels=set(original_tunnel_labels),
    project=project,
    passes=4,
    check_widths=True,
)
```

注意两个规则：

1. 模板里原本有的 Tunnel 默认保留
2. 你新加的 Tunnel 如果和模板 Tunnel 同名，也保留，因为它表示你在接模板语义接口

不要犯“看见内部 Tunnel 就批量删”的错误。  
第二次实验里先行进位电路就踩过这个坑：某些内部 Tunnel 去掉以后，电路图面更干净，但行为反而悄悄变了。


### 7.4 改位宽时，要把“多出来的那部分世界”一起删掉

很多人会犯一个错：只改保留下来的低位路径，却没把高位垃圾一起清走。

真正应该同步删除或修改的是：

1. 多出来的高位 `Tunnel`
2. 多出来的高位导线
3. 多出来的高位 `Splitter` 输出脚映射
4. 高位相关的探针、比较器、显示器
5. 高位相关的常量源和控制支路

一个实用原则是：

只要某一位在新任务里已经不存在，就把这一位相关的“组件 + 导线 + Tunnel + Splitter 映射”当成同一个删除单元一起处理。


## 8. 如何判断问题该修脚本还是修库

这是最重要的工程判断之一。

### 应该修任务脚本的情况

1. 某个特定电路的坐标选错了
2. 某个组件应该改宽度但你漏改了
3. 某个 Splitter 映射写错了
4. 某条本应删除的支路没有删除

### 应该修库的情况

1. 删除组件后，总会留下一堆断线
2. 修改后总会留下悬空 Tunnel
3. 加折线时容易生成斜线
4. 改 Splitter 时旧 `bitN` 属性残留
5. 几何/逻辑网解析在很多任务里都出现同类错误
6. 同一个 Tunnel 摆位补丁在每个任务脚本里都要重写一遍
7. 改位宽后总会留下高位残线或高位垃圾 Tunnel
8. 明明脚本没改错，但 GUI 里经常出现橙红导线位宽不匹配

判断原则很简单：

如果同一种问题会在很多电路任务里反复出现，它通常就不是脚本问题，而是库问题。

尤其要避免一种坏味道：

1. 库里有 `add_tunnel(...)`
2. 但任务脚本又各自私写一个 `add_tunnel(...)` 补丁版本
3. 不同脚本行为不一致

这会导致：

1. 某个实验通过了，换一个实验又退化
2. 调试经验不能沉淀
3. 别人拿到库单独用时，还是踩回原来的坑

更稳的做法是：

1. 先在任务里找到稳定行为
2. 再把稳定行为迁回模块 API
3. 最后让任务脚本只保留薄封装或直接调用模块


## 9. 编写 `validate` 脚本的总体思路

一份合格的 `validate` 脚本，最好同时覆盖 3 层验证。

还有一个原则：

不要迷信旧 `validate` 脚本。

如果下面任一条件成立，应该重写或重构验证脚本，而不是硬套旧脚本：

1. 主电路名变了
2. 子电路层次变了
3. 输入输出端口名或宽度变了
4. 你把“原电路重建”改成了“原电路增量修改”
5. 旧脚本强依赖某些已经不存在的坐标、Tunnel 标签或中间节点

验证脚本服务于“确认任务完成”，不是服务于“证明老脚本还能跑”。

### 第一层：基本可加载

验证目标：

1. 生成的工程能被成功读取
2. 主电路名正确
3. 不存在缺失的子电路引用

这层最简单，但必须有。


### 第二层：结构正确

验证目标：

1. 没有明显的断线、斜线、离网格坐标
2. 关键组件位宽正确
3. 关键主电路网没有串错
4. 不存在悬空 Tunnel
5. 不存在明显组件重叠

这层能帮你抓到“行为暂时没坏，但结构很脏”的问题。


### 第三层：行为正确

验证目标：

1. 对指定输入，输出和状态位符合预期
2. 无错误 / 单错 / 多错 等典型场景都覆盖
3. 主实验包装和核心子电路都经过验证

这层通常要靠 TTY 或自定义仿真夹具。


## 10. 如何设计一份通用的 `validate` 方案

写 `validate` 之前，先回答 4 个问题。

### 10.1 测谁

通常分两类：

1. 核心子电路
   例如编码器、解码器、ALU、寄存器组、控制器

2. 主实验封装
   例如主面板上显示、状态灯、比较器、Probe 是否仍然连接正确


### 10.2 测哪些输入

不要只测一种输入。

至少覆盖：

1. 一个正常输入
2. 一个边界输入，例如全 0、全 1、最高位为 1
3. 一个能触发状态变化的输入
4. 如果是纠错类电路，至少要有：
   - 无错
   - 单错
   - 多错或非法场景


### 10.3 测什么输出

输出通常分 3 类：

1. 主数据输出
2. 状态位输出
3. 中间观察值

如果任务里有“显示电路”，不要只测核心输出，也要测显示链是否保持一致。


### 10.4 怎样算通过

建议把检查分成：

1. 硬错误
   例如主输出不等于预期、位宽不对、主电路名错误

2. 结构可疑项
   例如 Tunnel 相关碰撞、某些布局非常挤

硬错误必须让脚本非零退出。
结构可疑项可以先报警，再人工复核。


## 11. 通用 `validate` 脚本骨架

下面给一份通用模板。

```python
from pathlib import Path

from logisim_logic import load_project

ROOT = Path(__file__).resolve().parent


def expect(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def check_project_basic(project_path: Path, expected_main: str, errors: list[str]) -> None:
    project = load_project(project_path)
    expect(project.main_circuit_name == expected_main, f"main circuit mismatch: {project.main_circuit_name!r}", errors)
    names = set(project.circuit_names())
    for circuit in project.circuits:
        for comp in circuit.components:
            if comp.lib is None:
                expect(comp.name in names, f"{circuit.name}: missing subcircuit {comp.name} @ {comp.loc}", errors)


def main() -> int:
    errors: list[str] = []
    check_project_basic(ROOT / "输出工程.circ", "主电路名", errors)
    if errors:
        for msg in errors:
            print(msg)
        return 1
    print("validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

然后再逐层往里面加结构检查和行为检查。


## 12. 如何写结构验证

### 12.1 检查是否离开 10 像素网格

```python
def check_grid(project, circuit_name: str, errors: list[str]) -> None:
    circuit = project.circuit(circuit_name)
    for comp in circuit.components:
        if comp.name == "Text":
            continue
        expect(comp.loc[0] % 10 == 0, f"{circuit_name}: off-grid component {comp.name}@{comp.loc}", errors)
        expect(comp.loc[1] % 10 == 0, f"{circuit_name}: off-grid component {comp.name}@{comp.loc}", errors)
    for wire in circuit.wires:
        for point in (wire.start, wire.end):
            expect(point[0] % 10 == 0, f"{circuit_name}: off-grid wire {wire}", errors)
            expect(point[1] % 10 == 0, f"{circuit_name}: off-grid wire {wire}", errors)
```


### 12.2 检查是否存在斜线

```python
def check_orthogonal_wires(project, circuit_name: str, errors: list[str]) -> None:
    circuit = project.circuit(circuit_name)
    for wire in circuit.wires:
        diagonal = wire.start[0] != wire.end[0] and wire.start[1] != wire.end[1]
        expect(not diagonal, f"{circuit_name}: diagonal wire {wire}", errors)
```


### 12.3 检查关键位宽

这类验证一定要写，因为行为不对时，位宽错误是最高频原因之一。

```python
def check_component_width(project, circuit_name: str, kind: str, loc: tuple[int, int], width: str, errors: list[str]) -> None:
    circuit = project.circuit(circuit_name)
    comp = next(c for c in circuit.components if c.name == kind and c.loc == loc)
    expect(comp.get("width") == width, f"{circuit_name}: {kind}@{loc} width={comp.get('width')!r}", errors)
```


### 12.4 检查橙红导线对应的位宽冲突

如果 Logisim GUI 里已经出现橙红色导线，那通常不是“布局不优雅”，而是结构已经错了。

优先用库里的诊断 API：

```python
from logisim_logic import find_invalid_wire_indexes, find_width_conflicts


def check_width_conflicts(project, circuit_name: str, errors: list[str]) -> None:
    circuit = project.circuit(circuit_name)
    bad_wires = find_invalid_wire_indexes(circuit, project=project)
    conflicts = find_width_conflicts(circuit, project=project)
    if bad_wires:
        errors.append(f"{circuit_name}: invalid wire indexes={bad_wires}")
    for conflict in conflicts:
        errors.append(
            f"{circuit_name}: width conflict kind={conflict.kind} "
            f"widths={conflict.widths()} points={sorted(conflict.points)}"
        )
```

这类错误最常见的来源是：

1. `Pin.width` 改了，但 `Tunnel.width` 没改
2. `Splitter.incoming` 改了，但旧 `bitN` 还在
3. 某段高位支路删了一半，留下残线连到了低位路径
4. 某个 `Gate` / `Comparator` / `Negator` / `Adder` 宽度没同步


### 12.5 检查是否有悬空 Tunnel

通用思路：

1. 枚举所有组件端口点
2. 对每个 Tunnel 看它的位置是否被导线覆盖，或是否与其他组件连接点重合
3. 两者都没有，就判定为悬空

如果你希望严格一点，再要求：

1. Tunnel 不仅要“碰到一根线”
2. 还应该能通向至少一个非 Tunnel 组件端点


### 12.6 检查逻辑网是否异常合并

用 `extract_logical_circuit()`。

特别适合检查：

1. 主实验里的 LED、Pin、Probe 是否意外上了同一张大网
2. 某些状态位和数据总线是否短接
3. 某些 Tunnel 是否把本不该合并的网合并了


### 12.7 检查组件碰撞

用 `find_component_overlaps()`。

建议默认：

1. 对 `Text` 忽略
2. 对 `Tunnel` 视库成熟度决定是否忽略

如果你的 Tunnel 几何模型还不成熟，建议：

1. `ignore_names={"Text", "Tunnel"}`
2. 另外自己写一套“悬空 Tunnel / 直接压在端口上的 Tunnel”检查


### 12.8 必要时把电路渲染成图片再看

当你已经知道“这里有问题”，但还说不清到底是：

1. Tunnel 方向错了
2. Tunnel 压住了元件
3. 导线穿过了元件
4. 某个密集区短路了

这时不要只看文字日志，直接做一张调试图。

最简办法有两种：

1. 用 Logisim GUI 打开目标子电路，肉眼看密集区域
2. 自己写一个很小的 PIL 调试渲染脚本，按 `component_bounds()`、组件坐标、导线段把电路画出来

如果你自己写调试渲染器，优先画：

1. 10 像素网格
2. 组件包围盒
3. 组件端口点
4. 导线
5. Tunnel 标签和朝向

这对排查“明明逻辑网不对，但看文本很难意识到哪里挤在一起了”非常有效。


## 13. 如何写行为验证

行为验证最稳妥的方式，是给“待测子电路”临时搭一个测试夹具，然后用 Logisim TTY 运行。

### 13.1 为什么建议写测试夹具

因为大多数原始主实验电路：

1. 含有显示器、时钟、随机干扰、按钮
2. 很难直接自动读输出
3. 不适合拿来做稳定回归测试

测试夹具的目标是：

1. 输入可控
2. 输出可读
3. 电路小
4. 失败时易定位


### 13.2 通用行为验证方案

通常步骤如下：

1. 读取输出工程
2. 新建一个临时 `tty_test` 子电路
3. 在 `tty_test` 里放：
   - 常量输入
   - 待测子电路实例
   - 输出 Pin
   - 一个恒为 1 的 halt Pin
4. 把 `tty_test` 设为主电路
5. 保存到临时文件
6. 运行：

```powershell
java -jar logisim-generic-2.7.1.jar -tty table,halt 临时文件.circ
```

7. 读取 TTY 输出，和预期比较

这里再强调一次：

1. 行为验证默认只用 `generic`
2. 不要拿 `evolution` 代跑，除非你已经确认工程和库都兼容它
3. 在调用 TTY 前，最好先做一次 `normalize_project_source(...)`

如果是带时序控制的电路，不要直接拿原主实验电路做 TTY。

更稳的策略是：

1. 只测核心子电路
2. 手动提供 `CLK`、`RST`、`END` 等控制输入
3. 让停机条件由测试夹具自己控制

否则你很容易遇到：

1. TTY 不停机
2. 输出混有显示层噪声
3. 主包装里的随机源、按钮、时钟把结果污染


### 13.3 用 `LogicCircuitBuilder` 快速搭夹具

示例：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess

from logisim_logic import load_project, save_project
from logisim_logic.logic_builder import LogicCircuitBuilder

ROOT = Path(__file__).resolve().parent
LOGISIM_JAR = ROOT / "logisim-generic-2.7.1.jar"

PIN_OUT = {
    "facing": "west",
    "output": "true",
    "tristate": "true",
    "pull": "none",
    "labelloc": "north",
    "labelfont": "Dialog plain 12",
    "labelcolor": "#000000",
}

SUBCKT = {
    "facing": "east",
    "label": "",
    "labelloc": "north",
    "labelfont": "Dialog plain 12",
    "labelcolor": "#000000",
}


def tty_table(path: Path) -> str:
    run = subprocess.run(
        ["java", "-jar", str(LOGISIM_JAR), "-tty", "table,halt", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise RuntimeError(run.stderr.strip() or "tty failed")
    lines = [line.strip() for line in run.stdout.splitlines() if line.strip()]
    return lines[0]


def build_harness() -> Path:
    project = load_project(ROOT / "输出工程.circ")
    builder = LogicCircuitBuilder("tty_test", project=project, allow_tunnel_fallback=True)

    builder.add_instance("in0", "Constant", {"width": "8", "value": "0x5a"}, loc=(80, 120))
    builder.add_instance("uut", "目标子电路", SUBCKT, loc=(260, 120))
    builder.add_instance("out0", "Pin", {**PIN_OUT, "width": "8", "label": "out"}, loc=(520, 120))
    builder.add_instance("haltc", "Constant", {"width": "1", "value": "0x1"}, loc=(460, 180))
    builder.add_instance("halt", "Pin", {**PIN_OUT, "width": "1", "label": "halt"}, loc=(520, 180))

    builder.connect("in0.io", "uut.输入端口名")
    builder.connect("uut.输出端口名", "out0.io")
    builder.connect("haltc.io", "halt.io")

    project.circuits.append(builder.build())
    project.set_main("tty_test")

    out_path = ROOT / "tmp_tty_test.circ"
    save_project(project, out_path)
    return out_path
```

这就是最基本的行为验证模板。


### 13.4 两种最常用的夹具模式：组合电路 / 时序电路

如果你的目标是从零复现类似第二次实验的 `validate_lab2_circuits.py`，一定要把这两种模式分开写。  
不要试图用同一份夹具模板硬测所有电路。

#### 13.4.1 组合电路：直接包一层常量输入和输出 Pin

适用对象：

1. 加法器
2. 组合乘法器
3. 编码器 / 解码器
4. 纯组合控制逻辑

推荐写成一个通用函数：

```python
def simulate_combinational(
    project_name: str,
    circuit_name: str,
    inputs: list[tuple[str, int, int]],
    outputs: list[tuple[str, int]],
):
    project = load_project(ROOT / project_name)
    builder = LogicCircuitBuilder("tty_case", project=project, allow_tunnel_fallback=True)

    for index, (label, width, value) in enumerate(inputs):
        builder.add_instance(
            f"in_{index}",
            "Constant",
            {"width": str(width), "value": hex(value)},
            loc=(80, 120 + index * 80),
        )

    builder.add_instance("dut", circuit_name, SUBCKT, loc=(320, 180))

    for index, (label, _, _) in enumerate(inputs):
        builder.connect(f"in_{index}.io", f"dut.{label}")

    for index, (label, width) in enumerate(outputs):
        builder.add_instance(
            f"out_{index}",
            "Pin",
            {**PIN_OUT, "width": str(width), "label": f"out_{index}"},
            loc=(760, 120 + index * 60),
        )
        builder.connect(f"dut.{label}", f"out_{index}.io")

    builder.add_instance("haltc", "Constant", {"width": "1", "value": "0x1"}, loc=(660, 120 + len(outputs) * 60))
    builder.add_instance("halt", "Pin", {**PIN_OUT, "width": "1", "label": "halt"}, loc=(760, 120 + len(outputs) * 60))
    builder.connect("haltc.io", "halt.io")

    project.circuits.append(builder.build())
    return run_generic(project, main_name="tty_case")
```

这里最重要的点有 4 个：

1. 组合电路用 `Constant` 喂输入，比手工写 Pin 再接值更稳
2. 输出全部接到 `Pin`，方便 `-tty table,halt` 直接读表
3. 再单独补一个恒为 1 的 `halt` Pin，让 TTY 稳定停机
4. `allow_tunnel_fallback=True` 可以降低某些布局下夹具连线失败的概率

#### 13.4.2 时序电路：不要直接测原主电路，要先打一个“自动驱动版”

适用对象：

1. 一位乘法器
2. 寄存器类实验
3. 需要 `RST` / `CLK` / `END` 的有限状态电路

原始主实验电路通常含有：

1. `Button`
2. `LED`
3. 人工时钟
4. 只能手点的复位路径

这类电路如果直接拿来 TTY，常见结果是：

1. 不停机
2. 读不到稳定输出
3. 结果受显示层和交互件干扰

更稳的模式是：

1. 先克隆主电路，做一个 `xxx_auto_validate`
2. 删除 `Button`
3. 加 `Clock`
4. 用 `NOT Gate` 或其他组合逻辑构造一次性 `RST`
5. 把 `END` 接到 `halt`
6. 再由外层 `tty_serial` 小夹具去喂 `X`、`Y`

第二次实验里比较稳定的一种模式如下：

```python
def patch_serial_auto_main(project, main_name: str) -> str:
    auto_name = f"{main_name}_auto_validate"
    circuit = deepcopy(project.circuit(main_name))
    circuit.name = auto_name
    circuit.components = [comp for comp in circuit.components if comp.name != "Button"]

    add_component(circuit, "Clock", (1580, 300), {"facing": "east", "highDuration": "1", "lowDuration": "1"}, lib="0")
    add_component(circuit, "Tunnel", (1620, 300), {"facing": "east", "width": "1", "label": "CLK"}, lib="0")

    add_component(circuit, "Clock", (1500, 360), {"facing": "east", "highDuration": "100", "lowDuration": "2"}, lib="0")
    add_component(circuit, "NOT Gate", (1600, 360), {"facing": "east", "width": "1", "size": "30"}, lib="1")
    add_component(circuit, "Tunnel", (1640, 360), {"facing": "east", "width": "1", "label": "RST"}, lib="0")

    add_component(circuit, "Tunnel", (1560, 420), {"facing": "east", "width": "1", "label": "END"}, lib="0")
    add_component(circuit, "AND Gate", (1660, 420), {"facing": "east", "width": "1", "size": "30", "inputs": "2"}, lib="1")
    add_component(circuit, "Pin", (1740, 420), {**PIN_OUT, "width": "1", "label": "halt"}, lib="0")

    project.circuits = [existing for existing in project.circuits if existing.name != auto_name]
    project.circuits.append(circuit)
    return auto_name
```

然后外层再包一层：

```python
def simulate_serial_top(project_name, main_name, x_width, y_width, product_width, x_value, y_value):
    project = load_project(ROOT / project_name)
    auto_name = patch_serial_auto_main(project, main_name)

    builder = LogicCircuitBuilder("tty_serial", project=project, allow_tunnel_fallback=True)
    builder.add_instance("x", "Constant", {"width": str(x_width), "value": hex(x_value)}, loc=(80, 120))
    builder.add_instance("y", "Constant", {"width": str(y_width), "value": hex(y_value)}, loc=(80, 220))
    builder.add_instance("dut", auto_name, SUBCKT, loc=(340, 180))
    builder.add_instance("prod", "Pin", {**PIN_OUT, "width": str(product_width), "label": "prod"}, loc=(840, 180))
    builder.add_instance("halt", "Pin", {**PIN_OUT, "width": "1", "label": "halt"}, loc=(840, 260))
    builder.connect("x.io", "dut.X")
    builder.connect("y.io", "dut.Y")
    builder.connect("dut.乘积", "prod.io")
    builder.connect("dut.halt", "halt.io")

    project.circuits.append(builder.build())
    return run_generic(project, main_name="tty_serial")
```

这个模式的优点是：

1. 不依赖手点按钮
2. `CLK` 和 `RST` 完全自动化
3. `END` 直接转成停机条件
4. 外层输入输出接口统一，回归测试非常好写


### 13.5 如何设计测试用例

不同任务，测试用例设计方式不同。

#### 缩位宽任务

至少测：

1. 普通值
2. 全 0
3. 全 1
4. 最高位为 1 的值

目标：

1. 看高位是否真的被删掉
2. 看符号位或最高位处理是否被误伤

#### 奇偶校验类任务

至少测：

1. 校验正确
2. 单位翻转后校验错误

目标：

1. 状态位正确
2. 数据位原样输出或按题意输出

#### 纠错类任务

至少测：

1. 无错
2. 单错
3. 双错或非法错

目标：

1. 状态位正确
2. 纠错后的数据正确
3. 不该纠错时不能误纠

#### 算术或逻辑运算类任务

至少测：

1. 正常输入
2. 进位/借位/溢出边界
3. 零结果
4. 最大值和最小值


### 13.6 预期值从哪里来

预期值不能拍脑袋写。

通常有 4 种来源：

1. 题目定义
2. 原始正确电路
3. 你自己写的 Python 参考实现
4. 少量手工推导 + GUI 复核

如果逻辑稍复杂，强烈建议先写一个纯 Python 参考函数。

例如：

```python
def ref_parity(x: int, width: int) -> int:
    ones = bin(x & ((1 << width) - 1)).count("1")
    return ones & 1
```

验证脚本只要比对“电路输出”和“参考函数输出”即可。


### 13.7 第二次实验这类题，至少要准备 4 类参考函数

只要任务涉及不同数制、不同位宽和不同标志位，验证脚本里最好先把这些参考函数写好。  
不要把编码、解码和预期值计算散落在每个 case 里。

#### 13.7.1 补码编码 / 解码

```python
def encode_twos(value: int, width: int) -> int:
    return value & ((1 << width) - 1)


def decode_twos(value: int, width: int) -> int:
    sign_bit = 1 << (width - 1)
    return value - (1 << width) if value & sign_bit else value
```

#### 13.7.2 原码编码 / 解码

```python
def encode_signmag(value: int, width: int) -> int:
    magnitude_width = width - 1
    magnitude = abs(value)
    if magnitude >= (1 << magnitude_width):
        raise ValueError((value, width))
    sign = 1 if value < 0 and magnitude != 0 else 0
    return (sign << magnitude_width) | magnitude


def decode_signmag(value: int, width: int) -> int:
    magnitude_width = width - 1
    sign = (value >> magnitude_width) & 1
    magnitude = value & ((1 << magnitude_width) - 1)
    if magnitude == 0:
        return 0
    return -magnitude if sign else magnitude
```

这组函数尤其重要，因为：

1. 原码存在负零，需要单独规整
2. 补码没有负零，零值处理方式不同

#### 13.7.3 TTY 输出规范化

`generic -tty table,halt` 打出来的总线经常带空格。  
建议统一先清洗：

```python
def normalize_tty_field(field: str) -> str:
    return field.replace(" ", "").strip()
```

否则你明明读到了正确位串，却会因为空格分组格式不同而误判失败。

#### 13.7.4 算术标志位参考实现

如果验证的是快速加法器，不要只比较和位，还要比较：

1. 进入最高位的进位
2. 总进位输出
3. 溢出位

通用写法如下：

```python
def expected_adder(width: int, x: int, y: int, c0: int) -> dict[str, int]:
    mask = (1 << width) - 1
    low_mask = (1 << (width - 1)) - 1
    xu = encode_twos(x, width)
    yu = encode_twos(y, width)
    full = xu + yu + c0
    sum_bits = full & mask
    carry_out = 1 if full >> width else 0
    carry_in_msb = 1 if ((xu & low_mask) + (yu & low_mask) + c0) >> (width - 1) else 0
    overflow = carry_in_msb ^ carry_out
    return {
        "sum": sum_bits,
        "carry_in_msb": carry_in_msb,
        "carry_out": carry_out,
        "overflow": overflow,
    }
```

第二次实验里，`16 位快速加法器` 和 `32 位快速加法器` 的验证都应该采用这种方式，而不是只看十进制求和结果。


## 14. 一份更完整的通用 `validate` 结构

推荐把 `validate.py` 分成这几类函数：

1. 工程加载与基础检查
2. 结构检查
3. 行为夹具构造
4. TTY 执行
5. 预期结果计算
6. 统一汇总和退出码

示意结构：

```python
def check_basic(...): ...
def check_grid(...): ...
def check_overlaps(...): ...
def check_orphan_tunnels(...): ...
def build_case_harness(...): ...
def run_tty(...): ...
def expected_for_case(...): ...

def validate():
    errors = []
    check_basic(...)
    check_grid(...)
    check_overlaps(...)
    check_orphan_tunnels(...)
    for case in CASES:
        got = run_case(case)
        expected = expected_for_case(case)
        if got != expected:
            errors.append(...)
    return errors
```

最后必须做到：

1. 有错时非零退出
2. 输出里明确说是哪一类检查失败
3. 最好能定位到电路名、组件坐标、测试用例名


## 15. 调试时的优先排查顺序

推荐固定这样查。

### 15.1 脚本生成失败

先查：

1. 子电路名是不是改错了
2. `component_at()` 坐标是不是找错了
3. 删除后是不是又在访问已删组件


### 15.2 GUI 一打开就报 XML 或版本错误

先查：

1. 根节点 `source` 是否被写坏
2. XML 属性值里是否混入了路径后缀、`.exe` 后缀之类脏数据
3. 保存脚本是否把引号、转义或文本属性写错


### 15.3 组件缺失，提示 `missing from library`

先查：

1. 你是不是拿错了 JAR，尤其是不是误用了 `evolution`
2. 组件 `lib` 编号是不是错了
3. 该组件在 generic 里是否属于另一个库编号
4. 你是不是复制了别的工程里的组件，但没同步库体系

经验上，这类问题先怀疑“JAR/库映射不兼容”，再怀疑电路本身。


### 15.4 生成成功，但 TTY 全是 `E`

先查：

1. 总线位宽不一致
2. 某个关键 `Multiplexer` / `Decoder` / `Gate` 的控制脚悬空
3. `Tunnel.width` 还是旧值
4. 某个保留组件虽然坐标没变，但属性还是旧值
5. `find_invalid_wire_indexes()` / `find_width_conflicts()` 是否已经报错


### 15.5 状态位对了，数据位错了

先查：

1. 数据路径是否改完整
2. 掩码/校验/纠错链上的宽度是否一致
3. 有没有旧导线把新数据路径污染了


### 15.6 GUI 里有橙红导线，或提示位宽不匹配

这时不要只靠肉眼猜，直接做 4 件事：

1. 跑 `find_invalid_wire_indexes()`
2. 跑 `find_width_conflicts()`
3. 检查相关 `Splitter` 的 `incoming`、`fanout`、`bitN`
4. 检查相关 `Pin`、`Tunnel`、门电路、比较器、求补器宽度是否同步

尤其是 `Splitter`，它经常是根因而不是表象。


### 15.7 结构验证报很多碰撞

分两类看：

1. 非 Tunnel 组件碰撞
   这是硬错误，优先修。

2. Tunnel 相关碰撞
   先看是不是：
   - Tunnel 压在元件上
   - Tunnel 过于密集
   - 还是单纯几何估算过大


### 15.8 有悬空 Tunnel 或残线

优先问自己：

1. 这是单个任务的特殊删改问题
2. 还是库的删除/清理逻辑本身太弱

如果同类问题在不同电路里反复出现，优先修库。

如果问题出在“我自己生成了太多中间 Tunnel”，优先按下面顺序处理：

1. 回头检查这些 Tunnel 是否真的需要语义名
2. 能直接改成 `connect_points_routed(...)` / `connect_ports_routed(...)` 的，先改直连
3. 需要保留语义名的，确认标签是否与模板接口同名
4. 只有模板里不存在的内部标签，才进入可选清理名单
5. 每做一轮清理后，重新跑位宽冲突检查和功能验证

不要一上来就把所有新增 Tunnel 批量删掉。

第二次实验里已经验证过，过于激进的去 Tunnel 操作可能出现两种后果：

1. 位宽仍然正确，但行为悄悄变错
2. 没有显式短路，却因为中间网络拓扑变化导致结果错误


### 15.9 看日志还是看不出来，就转成图

如果你已经知道“某一片区域有问题”，但纯文本定位太痛苦，就：

1. 打开 GUI 看那一片
2. 或者写一个调试渲染脚本，把该子电路导出成图片

排查布局类问题时，图片往往比 100 行日志更有用。


## 16. 什么时候需要怀疑库本身

只要遇到下面这些重复问题，就不要再只补任务脚本：

1. 删除组件后总留下断线
2. 改位宽后总留下高位垃圾 Tunnel
3. 加导线时偶尔生成斜线
4. Splitter 改映射后旧属性残留
5. 很多电路都出现同一种“几何判断失真”
6. Tunnel 方向经常算错
7. Tunnel 摆位总要靠任务脚本私补丁
8. 明明是同一种位宽冲突，但不同任务里总要人工修一遍

这时应该：

1. 在库里补更通用的清理逻辑
2. 让后续所有任务共享修复
3. 优先把修复放进公开 API，而不是藏在单个任务脚本内部

这比在每个任务脚本里打一堆临时补丁更划算。


## 17. 完成任务前的最终清单

在你认为“已经完成”之前，至少确认：

1. 输出 `.circ` 文件能正常加载
2. 主电路名正确
3. 子电路引用完整
4. 关键位宽全部正确
5. 没有斜线
6. 没有橙红导线或宽度冲突
7. 没有悬空 Tunnel
8. 关键非 Tunnel 组件没有碰撞
9. 行为测试通过
10. 典型边界输入通过
11. 用 `generic` JAR 实际打开过一次
12. 用 GUI 打开最终结果，人工看一遍密集区域


## 18. 一句话总结

用 `logisim_logic` 做电路任务，最稳的路线永远是：

1. 先分析任务和原电路。
2. 再设计“保留什么、删除什么、重接什么”。
3. 只改脚本解决任务特有问题。
4. 只要问题会跨任务重复出现，就回到库层修。
5. `validate` 一定要同时覆盖“结构”和“行为”。

如果你按这套流程做，即使面对一个完全陌生的电路任务，也能从 0 开始稳步推进到可交付结果。
