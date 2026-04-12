# logisim_logic API

这份文档是 `logisim_logic` 的完整接口手册。

目标是回答三类问题：

1. 这个接口是干什么的。
2. 应该在什么场景下用它。
3. 它和同层其它接口相比有什么区别。

说明：

- 本文按模块组织。
- 先写“包根命名空间稳定导出”，再写各子模块接口。
- 数据类的字段多数可直接从对象属性读取；这里重点解释“类的定位”和“方法的用途”。
- 一些接口参数很多，本文不逐个抄签名，而是重点说明语义和典型用法。

## 目录

- [根命名空间](#根命名空间)
- [xml_io.py](#xml_iopy)
- [model.py](#modelpy)
- [java_types.py](#java_typespy)
- [geometry.py](#geometrypy)
- [layout.py](#layoutpy)
- [graph.py](#graphpy)
- [logical.py](#logicalpy)
- [builder.py](#builderpy)
- [logic_builder.py](#logic_builderpy)
- [selection.py](#selectionpy)
- [template_tools.py](#template_toolspy)
- [high_level.py](#high_levelpy)
- [project_tools.py](#project_toolspy)
- [rom.py](#rompy)
- [compare.py](#comparepy)
- [rebuild_support.py](#rebuild_supportpy)

## 根命名空间

包根命名空间适合放“稳定、常用、跨模块的入口”。

### 读写与工程级操作

- `load_project(path)`
  - 读取一个 `.circ` 文件，返回 `RawProject`。
  - 这是几乎所有脚本的第一入口。
  - 用在“我要研究/修改一个现有工程”时。

- `save_project(project, path)`
  - 把 `RawProject` 写回到 `.circ` 文件。
  - 用在所有修改完成后的保存阶段。

- `clone_project(project)`
  - 深拷贝一个工程，适合“从原实验生成新实验文件”。
  - 用在不想破坏原项目对象时。

- `replace_circuit(project, circuit)`
  - 用新 `RawCircuit` 替换工程里同名的旧子电路。
  - 适合“已经重建好一个子电路，要塞回工程”。

- `remove_circuit(project, name)`
  - 从工程中删除指定名字的子电路。
  - 常用于清理中间调试电路或重命名前先删除冲突目标。

- `rename_circuit(project, old, new)`
  - 修改子电路名字，并维护工程内对应引用。
  - 用在“偶校验改奇校验”“实验 1 改 8 位版”这类任务。

- `set_main(project, name)`
  - 指定工程主电路。
  - 用在生成新工程后确保 Logisim 打开时落在正确页面。

### 底层数据模型

这些类是工程 XML 的保真表示。

- `RawProject`
  - 整个 Logisim 工程对象。
  - 持有所有子电路、库、选项、toolbar、main 等信息。

- `RawCircuit`
  - 单个子电路对象。
  - 持有元件、导线、appearance、item order 等。

- `RawComponent`
  - 单个元件实例。
  - 适合直接读写属性、位置、元件类型。

- `RawWire`
  - 单根正交导线。
  - 只表示一段水平线或竖直线。

- `RawAttribute`
  - 元件或工程节点上的属性键值。
  - 适合保留原始文本和值类型解释。

- `RawLibrary`, `RawTool`, `RawOptions`, `RawMappings`, `RawToolbar`, `RawToolbarItem`, `RawMain`, `RawMessage`, `RawAppearance`, `XmlFragment`, `CircuitPort`
  - 这些类分别对应工程 XML 里的其余结构。
  - 大多数情况下你不会单独构造它们，但当你想保真处理整个工程时会用到。

### 几何与布局

- `ComponentGeometry`
  - 单个元件的几何描述：边界、端口、方向相关信息。

- `PortGeometry`
  - 单个端口的几何描述：偏移、宽度、方向。

- `ComponentOverlap`
  - 两个元件边界冲突时的诊断对象。

- `PortAttachmentPlacement`
  - “把一个元件挂到另一个元件的某个端口边上”时的放置结果。

- `get_component_geometry(component, project=None)`
  - 恢复元件的真实端口和边界，是所有自动挂接、布局、逻辑抽取的基础。

- `get_component_visual_bounds(component, project=None)`
  - 计算视觉边界，适合碰撞检查和美观布局。

- `resolve_library_label(project, lib, name)`
  - 根据工程和库信息解析元件类别。

- `component_bounds(...)`, `component_extents(...)`, `expand_bounds(...)`, `combine_bounds(...)`, `spec_extents(...)`
  - 这些接口负责边界与尺寸计算。
  - 用在“先算体积，再布局，再避碰”这类场景。

- `default_splitter_pitch(...)`
  - 给 splitter 提供缺省位间距推断。

- `layout_row_locations(...)`, `layout_column_locations(...)`
  - 帮你把一组元件按行或按列排开。

- `attachment_facing_for_port(...)`
  - 推断“如果把新元件挂在这里，它应该朝哪个方向”。

- `place_attached_component(...)`
  - 自动求解一个附着元件的摆放位置和连接折线。
  - 这是 `attach_*_to_port()` 背后的几何核心。

- `find_component_overlaps(...)`
  - 找出当前电路里有哪些元件边界互相压住。

### 逻辑与建图

- `LogicCircuitBuilder`
  - 高层逻辑建图器。
  - 用“实例 + 连接关系”描述电路，自动完成布局和布线。

- `LogicInstanceSpec`, `LogicNetSpec`, `EndpointRef`
  - `LogicCircuitBuilder` 内部和外部都会用到的逻辑描述对象。

- `LogicalCircuit`
  - 从现有电路抽取出来的逻辑网表视图。

- `extract_logical_circuit(circuit, project=None)`
  - 把现有元件和导线提取成 `LogicalCircuit`。
  - 用在“我先理解原图逻辑结构，再决定怎么改”。

- `CircuitBuilder`
  - 比 `LogicCircuitBuilder` 更低层的轻量 builder。
  - 适合调试图、小测试图和手工构造底层对象。

- `component(...)`, `pt(...)`, `orthogonal(...)`
  - 用于快速构造元件、点和折线。

### 选择器与模板

- `ComponentSelector`
  - 一个可重复使用的元件选择规则。

- `SelectorView`
  - 把一组 selector 绑定到一个具体电路上之后的操作视图。

- `select_component(...)`, `select_tunnel(...)`
  - 快速生成 selector。

- `selector_view(circuit, selectors)`
  - 把 selector 字典绑定到某个电路。

- `CircuitTemplate`
  - “原图模板 + 当前修改电路 + 选择器”的高层封装。
  - 适合所有“在原有实验基础上修改”的脚本。

### 高级封装

- `ProjectFacade`
  - 工程级 façade。
  - 把“加载工程、复制子电路、导入子电路、设主电路、保存”收成更短的脚本接口。

- `CircuitEditor`
  - 电路级 façade。
  - 在 `CircuitTemplate` 之上再包一层，适合直接写“改 Probe、改 ROM、删旧文字、删一批元件、保留 appearance”这类动作。

### 导线图与比较

- `WireGraph`
  - 导线连通图。
  - 用于查看哪些点实际导通。

- `build_wire_graph(circuit)`
  - 从导线生成 `WireGraph`。

- `ProjectDiff`
  - 两个工程的差异对象。

- `project_signature(project)`
  - 生成工程签名，适合做快速比较或缓存键。

- `compare_projects(lhs, rhs)`, `compare_project_files(lhs_path, rhs_path)`
  - 对比两个工程或两个文件。

### ROM 与编码

- `rom_contents_from_words(...)`
  - 把整数 word 列表生成 Logisim ROM 的 `contents` 文本。

- `raw_contents_from_words(...)`
  - 生成更偏原始展示的 ROM 文本。

- `rom_words_from_contents(text)`
  - 把 `contents` 文本解析回整数 word 列表。

- `rom_image_from_contents(text, ...)`
  - 把 ROM 内容渲染成图像矩阵。

- `gb2312_word_stream(text)`
  - 把文本转成 GB2312 码流。

### Java 属性类型

- `Direction`
  - Logisim 方向类型。

- `Location`
  - Logisim 位置类型。

- `BitWidth`
  - Logisim 位宽类型。

## xml_io.py

这个模块负责 `.circ` 文件和 `RawProject` 之间的双向转换。

- `parse_point(text)`
  - 把 `"(x,y)"` 或等价文本解析成坐标。
  - 用在 XML 读入阶段，平时脚本很少直接调用。

- `format_point(point)`
  - 把坐标格式化回 Logisim 需要的文本。
  - 用在 XML 写出阶段，通常不手动调用。

- `load_project(path)`
  - 从磁盘读取工程，保留绝大多数底层细节。
  - 推荐作为一切处理的入口。

- `save_project(project, path)`
  - 把当前工程对象写回文件。
  - 会按模块内部模型回序列化 XML。

## model.py

这个模块定义了底层数据结构。你可以把它看成“可编辑的工程 AST”。

### 顶层辅助函数

- `point_to_location(point)`
  - 把普通 `(x, y)` 元组包装成 `Location` 对象。

- `shape_center(shape)`
  - 求 appearance 图形元素中心点。

- `rotate_bounds(bounds, from_dir, to_dir, xc, yc)`
  - 对边界做方向旋转。
  - 主要被 geometry/layout 层消费。

- `shape_bounds(shape)`
  - 计算 appearance 图形元素的边界。

### `XmlFragment`

通用 XML 片段。用于保留目前没有专门建模的 XML 子树。

- `from_element(elem)`
  - 从原始 XML 元素构造片段。
- `to_element()`
  - 回到 XML 元素。
- `to_dict()`
  - 转成便于调试的字典。

### `RawAttribute`

单个属性节点。

- `parsed()`
  - 按 `java_types.py` 规则解释成更接近 Logisim 语义的值。
- `to_dict()`
  - 转成调试字典。

### `RawTool`

库工具定义。一般出现在 `<lib>` 下。

- `attr_map()`
  - 取工具属性字典。
- `get(name, default=None)`
  - 按属性名取原始字符串值。
- `get_typed(name, default=None)`
  - 按属性名取已解析的值。
- `set(name, value, ...)`
  - 修改属性。
- `delete(name)`
  - 删除属性。
- `to_dict()`
  - 转成调试字典。

### `RawLibrary`

单个库定义。

- `tool(name)`
  - 取指定名字的工具。
- `to_dict()`
  - 转成调试字典。

### `RawOptions`

工程 options 节点。

- `attr_map()`, `get()`, `set()`, `delete()`, `to_dict()`
  - 行为和 `RawTool` 类似，只是作用在 options 上。

### `RawMappings`

工程 mappings 节点。

- `to_dict()`
  - 转成调试字典。

### `RawToolbarItem`

toolbar 里的单个条目。

- `to_dict()`
  - 转成调试字典。

### `RawToolbar`

整个 toolbar。

- `to_dict()`
  - 转成调试字典。

### `RawMain`

主电路定义数据类。

- 没有复杂方法，通常通过 `RawProject.set_main()` 间接使用。

### `RawMessage`

工程 message 节点。

- `to_dict()`
  - 转成调试字典。

### `RawAppearance`

子电路 appearance 节点。

- `to_dict()`
  - 转成调试字典。

### `RawWire`

单段导线。

- `to_dict()`
  - 转成调试字典。

### `RawComponent`

单个元件实例。最常用的底层对象之一。

- `attr_map()`
  - 返回属性字典。
- `get(name, default=None)`
  - 取属性原始值。
- `get_typed(name, default=None)`
  - 取属性解析值。
- `set(name, value, ...)`
  - 修改属性。
- `delete(name)`
  - 删除属性。
- `location()`
  - 返回 `Location`。
- `to_dict()`
  - 转成调试字典。

### `CircuitPort`

子电路端口抽象。常出现在 geometry/model 的端口推断过程中。

- `to_dict()`
  - 转成调试字典。

### `RawCircuit`

单个子电路。脚本里最常操作的对象之一。

- `attr_map()`, `get()`, `set()`, `delete()`
  - 读取或修改电路级属性。
- `find_components(...)`
  - 按条件找元件。
- `pin_components()`
  - 返回所有 Pin 元件。
- `iter_appearance_shapes()`
  - 遍历 appearance 图形。
- `resolved_item_order()`
  - 返回按 Logisim 语义解开的 item order。
- `explicit_port_offsets(facing=None)`
  - 读取 appearance 显式定义的端口偏移。
- `default_port_offsets(facing=None)`
  - 推断默认端口偏移。
- `port_offsets(facing=None)`
  - 获取最终端口偏移，是子电路挂接时最常用的端口来源。
- `explicit_appearance_offset_bounds(facing=None)`
  - appearance 显式边界。
- `default_appearance_offset_bounds(facing=None)`
  - 默认 appearance 边界。
- `appearance_offset_bounds(facing=None)`
  - 最终 appearance 边界。
- `to_dict()`
  - 转成调试字典。

### `RawProject`

整个工程对象。

- `circuit(name)`
  - 按名字取子电路。
- `has_circuit(name)`
  - 判断是否存在某个子电路。
- `main_circuit_name()`
  - 读取主电路名。
- `set_main(name)`
  - 设置主电路。
- `resolved_item_order()`
  - 解析工程级 item order。
- `to_dict()`
  - 转成调试字典。

## java_types.py

这个模块负责“字符串属性值”和“Logisim 语义值”之间的转换。

### `Direction`

表示方向。常见值如 `east/west/north/south`。

- `radians()`
  - 把方向转成弧度。
- `reverse()`
  - 取反方向。
- `get_right()`
  - 取当前方向右侧。
- `get_left()`
  - 取当前方向左侧。
- `parse(value)`
  - 从字符串解析方向。

### `Location`

表示位置。

- `parse(value)`
  - 从字符串解析位置。
- `translate(dx_or_dir, dy=None, right=None)`
  - 平移位置。
- `rotate(from_dir, to_dir, xc, yc)`
  - 围绕中心旋转位置。

### `BitWidth`

表示位宽。

- `parse(value)`
  - 从字符串或数值解析位宽。

### `AttributeOption`

属性选项载体。主要用于保留枚举式属性语义。

### `LogisimFont`

字体描述。

- `parse(value)`
  - 从 Logisim 字体字符串解析。

### `LogisimColor`

颜色描述。

- `parse(value)`
  - 从颜色文本解析。

### 顶层函数

- `infer_codec(name)`
  - 猜测文本编码。
- `parse_attribute_value(name, value)`
  - 按属性名解析值。
- `format_attribute_value(name, value)`
  - 按属性名格式化值。

## geometry.py

这个模块回答“元件在图上到底长什么样、端口在哪”。

### `PortGeometry`

单端口几何信息。

- 主要保存端口名、偏移、位宽、方向。
- 不含复杂方法，是几何结果载体。

### `ComponentGeometry`

单元件几何信息。

- `width()`
  - 返回几何宽度。
- `height()`
  - 返回几何高度。
- `absolute_bounds(loc)`
  - 给定元件放置位置，计算绝对边界。
- `port(name)`
  - 取某个端口的几何信息。
- `absolute_port(loc, name)`
  - 直接得到某个端口的绝对位置。

### 顶层函数

- `rotate_bounds(bounds, from_dir, to_dir, xc, yc)`
  - 旋转边界矩形。
- `get_component_visual_bounds(component, project=None)`
  - 计算视觉边界，适合碰撞检查。
- `get_component_geometry(component, project=None)`
  - 获取元件几何，是最核心的入口。
- `resolve_library_label(project, kind)`
  - 解析库标签和元件语义分类。

## layout.py

这个模块处理元件体积、相对布局、边界和路径搜索。

### `ComponentOverlap`

表示两个元件重叠。

- `intersection`
  - 返回交集边界。
- `describe()`
  - 返回易读的冲突说明文本。

### `PortAttachmentPlacement`

表示“一个元件挂到另一个元件端口边上”的求解结果。

- 包含锚点、引线点、最终 `loc`、朝向和折线路径。

### 顶层函数

- `expand_bounds(bounds, padding)`
  - 给边界加 padding。

- `combine_bounds(bounds_list)`
  - 合并多个边界为一个总边界。

- `component_bounds(component, project=None, padding=0, visual=False)`
  - 计算元件绝对边界。
  - `visual=True` 时更偏向图面体积。

- `component_extents(component, project=None, padding=0, visual=False)`
  - 计算元件相对原点的四向外延。
  - 常用于排版和相对定位。

- `spec_component(name, attrs, lib=None)`
  - 先不落地到电路，只按名字和属性造一个临时元件。
  - 用于“先估尺寸，再决定摆哪”。

- `spec_extents(spec, project=None, ...)`
  - 直接按规格计算体积。

- `layout_row_locations(specs, gap, start)`
  - 给一组规格生成一排横向位置。

- `layout_column_locations(specs, gap, start)`
  - 给一组规格生成一列纵向位置。

- `default_splitter_pitch(bus_width, appear='center')`
  - 估 splitter 缺省节距。

- `attachment_facing_for_port(component, port_name, project=None)`
  - 根据锚点端口推断附着元件面向方向。

- `route_circuit_path(circuit, start, goal, project=None, ...)`
  - 在现有电路障碍物之间找一条可行折线。
  - 适合调试单条线怎么走，不直接面对所有网络整体布线。

- `place_attached_component(circuit, anchor, port_name, attached, ..., attached_port_name=None)`
  - 自动求解附着元件位置。
  - 如果新元件有多个端口，`attached_port_name` 用来明确“锚点应该连到新元件哪一个端口”。

- `find_component_overlaps(circuit, project=None, ...)`
  - 检查当前电路有哪些元件边界冲突。

## graph.py

这个模块把导线变成可查询的连通图。

### `expand_wire(wire)`

- 把一段正交导线展开成网格点序列。
- 适合调试“这根线到底覆盖了哪些点”。

### `UnionFind`

并查集实现，用于导线并网。

- `add(item)`
  - 注册节点。
- `find(item)`
  - 查代表元。
- `union(a, b)`
  - 合并两个集合。

### `Net`

一个导线网络。

- `to_dict()`
  - 转成调试字典。

### `WireGraph`

导线图对象。

- `net_at(point)`
  - 如果该点唯一属于一条网络，返回网络 id，否则返回 `None`。
- `nets_at(point)`
  - 返回该点关联的所有网络 id。
- `nearby_points(point, radius=60)`
  - 找附近的导线点。
- `nearby_nets(point, radius=60)`
  - 找附近的网络。

### 顶层函数

- `build_wire_graph(circuit, split_points=())`
  - 从当前电路导线构造 `WireGraph`。
  - `split_points` 用来告诉图构建器“这些点虽然几何上重合，也应当被视为网络切分点”。

- `single_port_components()`
  - 返回默认按“单端口元件”处理的一组元件名字。

- `infer_component_attachment_points(component, graph, radius=60)`
  - 在不知道端口定义时，从附近导线反推一个元件可能接到了哪些点。
  - 用于一些几何信息不完整的元件。

## logical.py

这个模块把“原始图形结构”变成“逻辑网表结构”。

### `LogicalEndpoint`

逻辑端点，表示“某个实例的某个端口”。

- `to_dict()`
  - 转成调试字典。

### `LogicalInstance`

逻辑实例。

- 记录实例 id、类型、属性、位置、端口点和端口元信息。
- `to_dict()`
  - 转成调试字典。

### `LogicalNet`

逻辑网络。

- 记录网络 id、端点列表、覆盖点、tunnel 标签。
- `to_dict()`
  - 转成调试字典。

### `LogicalCircuit`

整个逻辑电路抽象。

- `to_dict()`
  - 转成调试字典。

### `extract_logical_circuit(circuit, radius=60, project=None)`

- 核心功能是“从当前图面恢复逻辑结构”。
- 输出的 `instances` 可回答“有哪些逻辑元件”。
- 输出的 `nets` 可回答“哪些端口属于同一条逻辑网络”。
- 对 tunnel 会额外收集 `tunnel_labels`，方便保留语义名。
- 适合做：
  - 理解原图
  - 导出网表
  - 对比重建前后逻辑结构

## builder.py

这个模块是一个偏底层的手工构造器，适合小图和调试。

### 基础函数

- `pt(x, y)`
  - 生成点元组。

- `hline(start, end_x)`
  - 生成水平导线路径。

- `vline(start, end_y)`
  - 生成竖直导线路径。

- `orthogonal(start, end, jog_x=None, jog_y=None)`
  - 生成正交折线。
  - 适合手工控制一条线的拐点。

- `attr(name, value, as_text=False)`
  - 快速生成 `RawAttribute`。

- `component(name, loc, attrs, lib=None)`
  - 快速生成 `RawComponent`。

### `CircuitBuilder`

这是“我知道要放什么元件，也愿意自己管坐标”的 builder。

- `add(comp)`
  - 把现成元件塞进 builder。

- `add_wire(start, end)`
  - 加一段导线。

- `add_path(start, end)`
  - 加一条折线路径。

- `pin(loc, ...)`
  - 快速放一个 Pin。

- `text(loc, text_value, font=...)`
  - 快速放一个 Text。

- `probe(loc, ...)`
  - 快速放一个 Probe。

- `led(loc, ...)`
  - 快速放一个 LED。

- `clock(loc, ...)`
  - 快速放一个 Clock。

- `button(loc, ...)`
  - 快速放一个 Button。

- `constant(loc, ...)`
  - 快速放一个 Constant。

- `comparator(loc, ...)`
  - 快速放一个 Comparator。

- `xor_gate(loc, ...)`
  - 快速放一个 XOR。

- `random(loc, ...)`
  - 快速放一个 Random。

- `rom(loc, ...)`
  - 快速放一个 ROM。

- `subcircuit(name, loc, ...)`
  - 快速放一个子电路实例。

- `build()`
  - 输出 `RawCircuit`。

## logic_builder.py

这个模块是当前最重要的高层建图接口。

### `EndpointRef`

端点引用，通常由 `实例ID.端口名` 解析而来。

- `parse(value)`
  - 把字符串端点解析成 `EndpointRef`。

### `LogicInstanceSpec`

逻辑实例规格。

- `raw_component(loc, project=None)`
  - 在指定位置生成底层 `RawComponent`。

### `LogicNetSpec`

逻辑网络规格。

- 记录一条待连网络的端点和标签信息。

### `RoutedNetPlan`

单条网络布线结果。

- 主要是内部规划结果容器。

### `WireOccupancy`

导线占用信息。

- `merged(other)`
  - 合并两份占用数据。
- `interior_directions(point)`
  - 查询某点内部导线方向。

### `LogicCircuitBuilder`

高层逻辑 builder。推荐把它当成“描述逻辑而不是坐标”的主入口。

- `add_instance(instance_id, kind, attrs, ...)`
  - 注册一个逻辑实例。
  - 常用附加参数包括：
    - `lib`
    - `loc`
    - `rank`
    - `track`
  - 如果给 `rank/track`，就是在给自动布局提供软约束。

- `connect(*endpoints, label=None, force_tunnel=False)`
  - 声明一条逻辑网络。
  - 端点一般写成 `"实例ID.端口名"`。
  - `label` 是语义名。
  - `force_tunnel=True` 表示允许/要求以 tunnel 语义表现。

- `build()`
  - 完成布局、布线并返回 `RawCircuit`。
  - 是逻辑 builder 的最终输出。

## selection.py

这个模块解决“别再用坐标硬找元件”的问题。

### `ComponentSelector`

一个可重用的元件选择规则。

- `matches(comp)`
  - 判断某个元件是否匹配。

- `resolve_all(circuit)`
  - 返回当前电路里所有匹配元件。

- `resolve(circuit)`
  - 返回一个匹配元件；如果没有或索引越界则报错。

### `select_component(kind=None, ..., contains=None, **attrs)`

- 快速构造 `ComponentSelector`。
- 适合按：
  - 元件种类
  - 属性精确值
  - 属性包含子串
  - 排序规则
  - 第几个匹配项
  来选择元件。

### `select_tunnel(label, ..., contains=None, **attrs)`

- `select_component()` 的 tunnel 专用快捷版。
- 常用来定位语义总线。

### `SelectorView`

把一组 selector 真正绑定到某个电路后的操作对象。

- `component(key)`
  - 取一个匹配元件。

- `components(key)`
  - 取所有匹配元件。

- `loc(key)`
  - 取元件位置。

- `attrs_copy(key)`
  - 复制元件属性字典。

- `set_attrs(key, **attrs)`
  - 批量改属性。

- `set_width(key, width)`
  - 改位宽。

- `set_probe(key, width=None, label=None, radix=None, facing=None)`
  - 改 Probe 常见属性。

- `replace_text(key, text)`
  - 改 Text 文本。

- `splitter_extract(key, incoming, selected)`
  - 把某个 splitter 改成“从总线中抽出指定比特”的形态。

### `selector_view(circuit, selectors)`

- 构造 `SelectorView`。

## template_tools.py

这个模块把“原图模板 + 当前修改电路 + 选择器”统一起来，是当前最适合写重建脚本的高层接口之一。

### `CircuitTemplate`

- `base`
  - 基于原图 `base_circuit` 的 `SelectorView`。
  - 适合从原图复制属性和锚点。

- `current`
  - 基于当前电路 `circuit` 的 `SelectorView`。
  - 适合直接修改当前电路。

- `view(source='current')`
  - 在 `base/current` 两个视图之间切换。

- `component(key, source='current')`
  - 取一个匹配元件。

- `components(key, source='current')`
  - 取所有匹配元件。

- `attrs_copy(key, source='base')`
  - 从某个源视图复制属性。

- `loc(key, source='base')`
  - 取元件位置。

- `set_attrs(key, **attrs)`
  - 改当前电路里的元件属性。

- `set_width(key, width)`
  - 改位宽。

- `set_probe(key, width=None, label=None, radix=None, facing=None)`
  - 改 Probe。

- `replace_text(key, text)`
  - 改 Text。

- `splitter_extract(key, incoming, selected)`
  - 让 splitter 只保留选中的位。

- `sorted_components(*keys, source='current', axis='y')`
  - 把一组元件按 `x` 或 `y` 轴排序。
  - 适合“从上到下”处理一排 Probe 或 Display。

- `add_builder_instance(builder, instance_id, key, ..., anchor=True)`
  - 把原图中的某个元件规格直接映射成 builder 里的一个实例。
  - 特别适合“沿用原图 Pin，但中间逻辑重建”。

- `connect_side_tunnel(key, side, label, width, source='base')`
  - 在原图某个元件侧边挂一个 tunnel。
  - 适合快速给外壳元件接语义信号名。

- `set_rom_words(key, addr_width, data_width, words, source='current')`
  - 改某个 ROM 的内容。

- `attach_component(anchor_key, port_name, name, attrs, ..., attached_port_name=None)`
  - 把一个普通元件挂到锚点端口边上。

- `attach_subcircuit(anchor_key, port_name, name, ..., attached_port_name=None)`
  - 把一个子电路挂到锚点端口边上。

- `attach_bit_extender(anchor_key, port_name, ..., attached_port_name='in')`
  - 把一个 `Bit Extender` 挂到锚点端口边上。

## high_level.py

这个模块提供一层更接近“脚本任务描述”的 façade，目标是让用户尽量少碰 `RawProject` / `RawCircuit` 细节。

### `ProjectFacade`

- `ProjectFacade.load(path)`
  - 从 `.circ` 文件直接得到工程级 façade。
  - 适合大多数脚本作为第一入口。

- `clone()`
  - 深拷贝整个工程，并保留原始来源路径信息。
  - 适合“从原实验派生一个新实验”。

- `circuit_names()`
  - 列出工程中的所有子电路名。
  - 适合脚本开始时做结构确认。

- `circuit(name)`
  - 直接返回底层 `RawCircuit`。
  - 用在你确实需要访问底层对象时。

- `edit_circuit(name, selectors=None, base_name=None)`
  - 返回一个 `CircuitEditor`。
  - 常用于“在当前工程里编辑某个子电路”。
  - `base_name` 允许你指定一个不同的模板来源，例如“当前电路已经改过，但仍想用原电路当 base”。

- `clone_circuit(source_name, new_name, ...)`
  - 复制工程中的一个子电路并立刻放回当前工程。
  - 适合“保留原电路，再派生出一个新名字版本”。

- `import_circuit_from(other, source_name, as_name=None, ...)`
  - 从另一个工程导入某个子电路并替换/加入到当前工程。
  - 很适合“原工程做微调，复杂内核直接移植已调好的子电路”。

- `import_circuit_file(path, source_name, as_name=None, ...)`
  - 直接从另一个 `.circ` 文件导入某个子电路。
  - 当你只想“拿某个 donor 文件里的一个子电路”时，这比先手动 `ProjectFacade.load()` 更短。

- `rename_circuit(old_name, new_name)` / `remove_circuit(name)` / `replace_circuit(circuit)` / `set_main(name)`
  - 这些是工程级常见操作。
  - 相比直接操作 `project_tools.py`，它们更方便和统一。

- `normalize_root_padding(...)`
  - 对工程根电路做最小 padding 归一化。
  - 适合保存前统一把负坐标或贴边问题收掉。

- `save(path=None, normalize_root_padding=False, ...)`
  - 保存当前工程。
  - 如果 façade 是从文件加载出来的，可以不再重复传路径。

### `CircuitEditor`

- `add_selector(key, selector)` / `select_component(key, kind=None, ...)`
  - 往当前编辑器里注册选择器。
  - 适合脚本里逐步把“逻辑上的元件角色”绑定起来。

- `component(key, source=...)` / `components(key, source=...)`
  - 根据选择器拿单个或多个元件。
  - `source="base"` 表示从模板来源取，`source="current"` 表示从当前电路取。

- `attrs_copy(key, source=...)` / `loc(key, source=...)`
  - 复制原属性、读取参考位置。
  - 适合“在原图风格基础上继续构造”。

- `set_attrs(...)` / `set_width(...)` / `set_probe(...)` / `replace_text(...)`
  - 这些是最常见的小修改动作。
  - 用来改 Pin、Probe、Text 非常直接。

- `replace_text_exact(old, new)` / `update_text_contains(old, new)`
  - 面向整个子电路的批量文字替换。
  - 比逐个 selector 找 Text 更适合改实验标题、说明文字。

- `set_rom_words(...)`
  - 直接把整数 word 列表写进 ROM。
  - 适合汉字编码实验和各种查表类子电路。

- `splitter_extract(...)`
  - 修改 splitter 只抽取哪些位。
  - 适合从 16 位裁到 8 位这类场景。

- `labeled_components(...)`
  - 列出当前电路里所有带 label 的元件。
  - 很适合刚接手一个老电路时快速摸结构。

- `summary(...)`
  - 返回当前电路元件种类统计。
  - 适合调试和脚本自检。

- `remove_selected(...)` / `remove_where(...)`
  - 删除一批元件，并可顺带剪掉碰到这些端点的导线。
  - 适合清理原图说明文字、旧 tunnel、失效元件等。

- `preserve_appearance_from_base()`
  - 用 base 电路的 appearance 覆盖当前电路。
  - 适合重建过内部逻辑但仍希望封装外观不变的场景。

- `normalize_padding(...)`
  - 只对当前子电路做 padding 归一化。
  - 适合局部编辑后快速收口。

## project_tools.py

这个模块只关心“工程里有哪些子电路，怎么换、删、改名字”。

- `replace_circuit(project, circuit)`
  - 用同名新电路替换旧电路。

- `remove_circuit(project, name)`
  - 删除一个子电路。

- `rename_circuit(project, old_name, new_name)`
  - 改子电路名字并处理引用。

- `set_main(project, name)`
  - 设置主电路。

- `clone_project(project)`
  - 深拷贝工程。

## rom.py

这个模块专门处理 Logisim ROM 的 `contents` 文本和字流转换。

- `rom_contents_from_words(addr_width, data_width, words, row_size=...)`
  - 把整数 word 序列编码成 Logisim ROM 内容文本。
  - 适合真正写回 ROM。

- `raw_contents_from_words(words, data_width, row_size=...)`
  - 生成更直观的原始文本形式。
  - 适合导出给人看。

- `rom_words_from_contents(contents)`
  - 解析 `contents` 文本，得到地址宽度、数据宽度和 word 列表。

- `rom_image_from_contents(contents, ...)`
  - 把 ROM 内容渲染成图像矩阵。

- `gb2312_word_stream(text)`
  - 把文本编码成 GB2312 16 位字流。

## compare.py

这个模块用来比较工程差异。

- `project_signature(project)`
  - 生成一个适合快速比较的工程签名。

- `normalize_xml_file(path)`
  - 把 XML 做规范化，便于比较。

- `ProjectDiff`
  - 差异结果对象。
  - 适合在脚本里统一展示对比结果。

- `compare_projects(left, right)`
  - 比较两个 `RawProject`。

- `compare_project_files(left, right)`
  - 直接比较两个 `.circ` 文件。

## rebuild_support.py

这是“重建脚本工具箱”。大多数脚本级、实验级、逻辑级辅助都在这里。

### 命名与批量连线

- `unique_label(prefix)`
  - 生成唯一信号名。
  - 用在需要自动创建 tunnel/中间信号而不想手写名字时。

- `connect_signal_fanout(builder, signal_fanout, forced_signals=None)`
  - 批量把“信号名 -> 端点列表”的映射送入 `LogicCircuitBuilder`。
  - 适合门级电路里一次性声明很多网络。

### 电路壳、appearance 与整体移动

- `detunnelize_circuit(circuit, keep_labels=None, project=None, passes=2)`
  - 尝试把同名 tunnel 对重新还原成真实导线。
  - 用于减少不必要的 tunnel。

- `snap10(value)`
  - 对齐到 Logisim 常见 10 像素网格。

- `clone_circuit(circuit, name=None)`
  - 深拷贝子电路。

- `circuit_shell(base, name=None, keep_names=None)`
  - 从原电路剥离出“壳子”。
  - 常用来保留 Pin/Text 外壳，只重建内部。

- `preserve_base_appearance(base, circuit)`
  - 把原图 appearance 拷到新电路，并回绑端口。

- `translate_circuit(circuit, dx, dy)`
  - 整体平移电路里的元件和导线。

- `align_circuit_pins_to_base(base, circuit)`
  - 尝试让当前电路的端口对齐原图端口。
  - 适合“外部端口必须继续兼容原实验主图”的场景。
  - 它关注的是端口兼容，不负责给内部导线留 padding。

- `normalize_circuit_to_padding(circuit, project=None, grid=10, padding=20)`
  - 把单个电路整体平移到指定 padding 内，避免元件或导线贴到左边界/上边界。
  - 适合“内部结构是新重建的，但希望图面至少离边缘留出安全距离”的场景。
  - 和 `align_circuit_pins_to_base()` 的区别是：
    它解决的是图面边界问题，不解决和原 appearance 的端口对齐问题。

- `normalize_project_root_circuits_to_padding(project, grid=10, padding=20)`
  - 对一个工程里“没有被其它子电路引用的根电路”统一做 padding 归一化。
  - 适合保存前最后收尾，避免主实验图或实验 2 页面有导线贴边、越界。
  - 它不会改被别的电路实例化的子电路，避免破坏已有封装端口位置。

- `merge_logic_circuit(base, built, name, keep_names=None)`
  - 把 builder 生成的逻辑电路合并到原图壳子里。

- `reflow_attached_components(circuit, project=None)`
  - 重新计算之前用 `attach_*` 挂上去的元件位置与导线。

- `reflow_bus_tunnels(circuit)`
  - 重新调整总线 tunnel 的摆放。

- `rename_tunnel_labels(circuit, old, new)`
  - 改 tunnel 标签名。

### 属性与检索

- `get_attr(comp, name, default=None)`
  - 取属性字符串。

- `attrs_dict(comp)`
  - 返回属性字典。

- `set_attr(comp, name, value, as_text=None)`
  - 改属性。

- `find_component(circuit, name=None, loc=None)`
  - 通过名字或坐标找元件。

- `find_tunnel(circuit, label, loc=None)`
  - 找指定标签的 tunnel。

- `replace_text_exact(circuit, old, new)`
  - 把某条 Text 完全匹配替换掉。

- `update_text_contains(circuit, old, new)`
  - 在 Text 中做子串替换。

### 基础构造

- `add_component(circuit, name, loc, attrs, lib)`
  - 直接加一个元件。

- `component_template(name, attrs, lib=None)`
  - 只构造元件模板，不塞进电路。

- `add_wire(circuit, start, end)`
  - 加一段线。

- `add_polyline(circuit, points)`
  - 加一条折线。

- `add_tunnel(circuit, loc, label, width, facing='west')`
  - 加一个 tunnel。

- `add_constant(circuit, loc, width, value, facing='east')`
  - 加一个 Constant。

- `add_bit_extender(circuit, loc, in_width, out_width, extend_type='zero')`
  - 加一个 Bit Extender。

- `add_logic_gate(circuit, gate_name, loc, ...)`
  - 加门电路。

- `add_not_gate(circuit, loc, ...)`
  - 加 NOT。

- `add_multiplexer(circuit, loc, ...)`
  - 加多路选择器。

- `add_decoder_component(circuit, loc, ...)`
  - 加 Decoder。

- `add_subtractor_component(circuit, loc, ...)`
  - 加 Subtractor。

- `add_subcircuit_instance(circuit, name, loc, ...)`
  - 加子电路实例。

- `add_constant_source(circuit, loc, width, value, ...)`
  - 加常量源。

### 端口与挂接

- `component_port_point(component, port_name, project=None)`
  - 求一个端口的绝对坐标。

- `component_port_lead(component, port_name, distance=20, project=None)`
  - 求端口和端口外延引线点。

- `add_tunnel_to_port(circuit, component, port_name, label, width, project=None)`
  - 在某个端口边上直接挂 tunnel。

- `add_constant_to_port(circuit, component, port_name, width, value, project=None)`
  - 在端口边上直接挂 Constant。

- `attach_component_to_port(circuit, anchor, port_name, name, attrs, ..., attached_port_name=None)`
  - 把一个普通元件挂到某个锚点端口边上，自动摆放并连线。
  - `attached_port_name` 用来指定新元件应该用哪个端口接过来。

- `attach_subcircuit_to_port(circuit, anchor, port_name, name, ..., attached_port_name=None)`
  - `attach_component_to_port()` 的子电路版本。

- `attach_bit_extender_to_port(circuit, anchor, port_name, ..., attached_port_name='in')`
  - `Bit Extender` 专用挂接。
  - 通常应显式指定 `attached_port_name='in'`。

- `attach_not_gate_to_port(circuit, anchor, port_name, ..., attached_port_name='in')`
  - NOT 专用挂接。

- `place_component_near_component(circuit, anchor, attached, side, ...)`
  - 在某个元件旁边找不冲突的位置，但不自动加线。

- `add_component_near_component(circuit, anchor, name, attrs, side, ...)`
  - 在某元件旁边加一个普通元件，不自动接线。

- `add_subcircuit_near_component(circuit, anchor, name, side, ...)`
  - 在某元件旁边加一个子电路，不自动接线。

- `connect_pin_to_tunnel(circuit, pin_loc, tunnel_loc, label, width, facing)`
  - 手工把 Pin 和 Tunnel 连接起来。

- `wire_pin_to_led(circuit, pin_loc, led_loc, ...)`
  - 手工把 Pin 和 LED 连起来。

### 元件属性工厂

- `single_bit_splitter_attrs(bus_width, bit_index, facing='east')`
  - 生成抽取单比特 splitter 的属性。

- `ordered_splitter_attrs(incoming, output_bits, facing='east', appear='center')`
  - 生成一个按给定位序映射的 splitter 属性。

- `logic_gate_attrs(gate_name, ...)`
  - 生成门电路属性。

- `multiplexer_attrs(...)`
  - 生成多路选择器属性。

- `decoder_attrs(...)`
  - 生成解码器属性。

### 逻辑子图拼装

- `append_built_circuit(target, built)`
  - 把 builder 生成的内容追加进一个现有电路壳子。

- `add_multi_input_gate_from_tunnels(circuit, gate_name, loc, input_labels, out_label)`
  - 从若干 tunnel 输入搭一个多输入门。

- `add_binary_gate_from_tunnels(circuit, gate_name, loc, in_a, in_b, out_label)`
  - 从两个 tunnel 输入搭一个二输入门。

- `add_not_from_tunnel(circuit, loc, in_label, out_label)`
  - 从一个 tunnel 输入搭 NOT。

- `chain_reduce(circuit, gate_name, input_labels, output_label)`
  - 把一组输入链式规约成一个输出。

- `add_compare_const_eq(circuit, loc, ...)`
  - 搭一个“是否等于常量”的比较结构。

- `add_reduction_tree_from_tunnels(circuit, gate_name, input_labels, output_label)`
  - 用树形结构做规约。

### 总线拆装

- `add_extract_cluster(circuit, ...)`
  - 加一组“从总线抽比特”的结构。

- `add_assemble_cluster(circuit, ...)`
  - 加一组“把单比特重新拼回总线”的结构。

- `extract_bus_labels(circuit, ...)`
  - 从一个总线信号拆出各位，并给每一位生成语义 label。

- `assemble_bus_from_labels(circuit, ...)`
  - 根据位映射把若干单比特 label 组回一个总线。

- `add_two_way_splitter(circuit, loc, ...)`
  - 加一个二路 splitter。

- `remove_components_at_locs(circuit, locs)`
  - 按坐标删除元件。

- `splitter_selected_bits(comp)`
  - 读取当前 splitter 实际抽了哪些位。

- `set_splitter_single_bit(comp, incoming, bit_index)`
  - 把 splitter 改成只抽 1 位。

- `set_splitter_two_way(comp, incoming, low_bits, high_bits)`
  - 把 splitter 改成两路拆分。

- `set_splitter_extract(comp, incoming, selected)`
  - 把 splitter 改成抽指定若干位。

- `add_named_bus_rebuilder(circuit, ...)`
  - 用命名信号重建总线。

- `add_bus_rebuilder_from_input_bus(circuit, ...)`
  - 从输入总线出发搭一个重建器。

## 使用建议

- 想研究原图：
  - `load_project()`
  - `project.circuit(name)`
  - `circuit.components`
  - `get_component_geometry()`
  - `build_wire_graph()`
  - `extract_logical_circuit()`

- 想在原图基础上改：
  - `select_component()`
  - `selector_view()`
  - `CircuitTemplate`
  - `replace_circuit()`

- 想从逻辑关系重建：
  - `LogicCircuitBuilder`
  - `connect_signal_fanout()`
  - `ordered_splitter_attrs()`
  - `logic_gate_attrs()`

- 想自动挂接新元件：
  - `attach_*_to_port()`
  - `add_*_near_component()`
  - 尽量显式给 `attached_port_name`

## 文档边界

- 这份文档覆盖当前模块里的公开接口。
- 某些内部辅助函数虽然可以调用，但没有承诺长期稳定。
- 如果后面你希望继续演进这套库，推荐把最常用的 `rebuild_support.py` 接口逐步上提到包根命名空间，并把 API 文档中的“脚本级工具”和“稳定公共 API”再分开。
