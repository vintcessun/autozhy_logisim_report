# 数据流转管线

本文件定义了 `autozhy_logisim_report` 系统内部的数据转换逻辑、XML 语义解析标准以及从电路组件到实验报告章节的自动化映射算法。

## 1. 原始输入层：Logisim XML (.circ) 深度解析

系统通过解析 `.circ` 文件的 XML 树来构建电路的逻辑拓扑。对于复杂的电路（如 `32位快速加法器.circ`），解析器重点提取以下具有高语义价值的组件：

### 1.1 关键组件提取清单

- **`<comp name="Pin">` (引脚)**:
  - **识别逻辑**: `tristate="false"` 判定为输入，`output="true"` 判定为输出。
  - **命名规范**: 必须识别 `label` 属性。若包含 `EnableCard` 后缀（类名风格），则判定为控制使能信号。
- **`<comp name="Tunnel">` (隧道)**:
  - **识别逻辑**: 提取 `label` 和 `loc`。
  - **拓扑合并**: 在构建逻辑图时，相同 Label 的所有隧道被视为逻辑上的同一个节点。
- **`<comp name="Probe">` (探针)**:
  - **识别逻辑**: 提取 `label` 和 `radix`（进制）。
  - **分析权重**: 若 `radix="10signed"`，验证智能体在分析结果时必须告知大模型此为“有符号十进制”，以确保溢出分析的准确性。
- **`<comp name="Splitter">` (分线器)**:
  - **语义解析**: 提取 `incoming` 和 `bitx`。用于推导总线（如 32 位地址线）如何被拆解为功能性片段。

### 1.2 XML 语义处理示例 (基于加法器电路)

当系统检测到 `32位快速加法器` 中的 `Tunnel` 逻辑时，解析引擎会执行以下映射：

XML

```
<comp lib="0" loc="(890,70)" name="Tunnel">
  <a name="width" val="32"/>
  <a name="label" value="X"/>
</comp>
```

------

## 2. 中间态：电路结构 JSON (CircuitSchema)

为了降低云端模型的 Token 消耗并提升 `Design Experiment Agent` 的校验效率，系统会将冗长的 XML 转化为紧凑的 `CircuitSchema`。

### 2.1 Schema 数据结构

JSON

```
{
  "circuit_metadata": {
    "filename": "32_bit_adder.circ",
    "has_clock": false
  },
  "logical_nodes": [
    {
      "id": "node_1",
      "type": "Pin",
      "label": "X",
      "bit_width": 32,
      "is_input": true
    },
    {
      "id": "node_2",
      "type": "Probe",
      "label": "Result_P",
      "radix": "10signed"
    }
  ],
  "connectivity": [
    {"source": "X_tunnel_group", "target": "FA_group_0", "semantic": "data_bus"}
  ]
}
```

------

## 3. 映射逻辑：电路特征 -> 报告章节 (Mapping Rules)

`Report Writing Agent` 拥有一套映射矩阵，根据电路中识别到的核心组件集合，自动决定将其写入 `example_output.md` 的哪个章节。

### 3.1 自动章节定位

| **识别特征 (Key Components)**  | **匹配关键词 (Match Patterns)** | **报告目标章节 (Target Section)** |
| ------------------------------ | ------------------------------- | --------------------------------- |
| `Splitter` + `FA/CLA`          | 加法器、并行、串行              | `## 3.1 (2) 16位快速加法器验证`   |
| `ROM` / `RAM`                  | 存储器、同步、异步              | `## 3.1 (1) 存储器组件验证`       |
| `Splitter (Tag/Index)` + `RAM` | Cache、映射、命中               | `## 3.1 (3) Cache 验证实验`       |
| 包含 `Actor-Critic` 闭环日志   | 设计、修改、自建                | `## 3.2 设计性实验`               |

### 3.2 图片路径映射

- **电路设计图**: 来自 `Design Agent` 完成闭环后的全景截图。
- **实验结果图**: 来自 `Verification Agent` 操作完成后的局部 `Probe/Pin` 截图。
- **存储路径**: 统一存放在 `output/{experiment_name}.assets/` 下，使用相对路径引用。

------

## 4. 闭环执行管线 (The Closed-Loop Pipeline)

1. **解析阶段**: `Content Parsing Agent` 提取 PDF 任务和 `.circ` XML。
2. **设计阶段 (若需要)**:
   - `Design Agent` 的子智能体修改 XML。
   - **静态校验**: Python 脚本扫描 XML，确保每一条 `<wire>` 的 `from/to` 坐标合法，且所有 `Tunnel` 均有对应连接点（**无视觉消耗**）。
3. **验证阶段**:
   - `Verification Agent` 调用 `ui-tars` 识别界面，执行 `pyautogui` 操作。
   - 根据电路中的 `Probe` 标签，仅对结果区域进行精准截图。
4. **汇总阶段**:
   - `Report Writing Agent` 调度 `gemini-3-flash` 处理杂活。
   - 加载 `./prompts/report/analysis_refinement.txt` 生成最终分析。

------

## 5. 提示词与调试标准 (Prompts & Debug)

### 5.1 提示词仓库 (`./prompts`)

- `/parsing/`: 任务拆解与题目关联模板。
- `/design/`: XML 修改指令与 Actor-Critic 反馈模板。
- `/verification/`: UI-TARS 操作序列指令。
- `/report/`: 最终 Markdown 格式填充与文字润色模板。

### 5.2 调试转储 (`./debug`)

在管线运行的任何阶段，中间状态均会序列化到 `debug/` 文件夹：

- `debug/screenshot/`: 记录每一轮视觉识别的原始大图。
- `debug/xml_diff/`: 记录设计实验中 XML 的变动历史。
- `debug/task_routing.log`: 记录题目与 `.circ` 文件的匹配日志。

