# autozhy_logisim_report

基于多智能体（Multi-Agent）协作的 Logisim 实验全自动化工具。投喂实验指导书（PDF / DOCX）与学生电路（`.circ`），自动产出完整 Markdown 实验报告、截图与可提交的电路归档，全程无需人工。

核心特性：

- **ReAct + Function Calling**：验证 / 设计智能体以 Gemini Pro 的原生工具调用能力驱动 Logisim Headless 仿真器，一步一步"观察—思考—动作"。
- **多轮自愈**：每个验证任务最多 **10 次**目标校验重试，送审不过则清理产物重跑 ReAct；单次 ReAct 会话内工具调用上限 128。
- **全链路断点续跑**：解析结果、子任务分解、每个任务的 ReAct 结果都落盘缓存，**中途任意步骤崩溃、网络 429、或手动 Ctrl-C，重新运行 `uv run main.py` 即可从上次位置继续**。
- **结构化报告**：直接读取指导书 DOCX 大纲，按原文的 3.1 / 3.2 / 3.3 等章节骨架组装 Markdown，并逐题回答思考题。

---

## 智能体流水线

主流程 [main.py](main.py) 按顺序串联四个智能体：

### 1. ContentParsingAgent &mdash; [src/agents/content_parsing.py](src/agents/content_parsing.py)

- 递归解压 `data_in/` 下的压缩包，自动处理 ZIP 内中文文件名（GBK fallback）。
- 调用 LLM 两阶段解析：**阶段一**从全文识别实验模块并分类（验证 / 设计 / 挑战）；**阶段二**把验证类进一步拆成原子测试切片。
- 输出 Pydantic 模型 `ParsingResult`（含 `verification_tasks` / `design_tasks`）并落盘 `cache/parsing_result.json`。

### 2. VerificationAgent &mdash; [src/agents/verification_agent.py](src/agents/verification_agent.py)

- 加载 [prompts/verification/blueprint.txt](prompts/verification/blueprint.txt)，启动 ReAct 循环；模型通过若干工具（`get_io` / `get_value` / `set_value` / `tick_until` / `load_memory` / `get_screenshot` / `report_verdict` 等）与仿真器交互。
- `load_memory` 只需填**文件名**，自动映射到 `workspace/` 下真实路径，省去模型构造绝对路径出错的机会。
- 每次尝试先用 Flash 模型切换到目标子电路（[prompts/verification/switch.txt](prompts/verification/switch.txt)）。
- 模型主动调用 `report_verdict` 收尾；随后抓取最终截图 → 生成文字分析 → 再用 Pro 模型做**场景感知的目标校验**（允许 `xxxxxxxx` 等高阻 / 未知态作为合理结果，当任务本身就是验证隔离 / 断开时）。
- 任一步校验不过（`goal_reached=false` 或 judge `goal_met=false`）立即清理本次产物，进入下一次尝试（总共 10 次）。

### 3. DesignAgent &mdash; [src/agents/design_agent.py](src/agents/design_agent.py)

- 对**参考电路**先跑一次仿真并截图，保存为 `reference_<任务名>.png`。
- 将学生待提交电路拷贝归档到 `output/提交电路/`。
- 用 Flash 模型（[prompts/design/decompose.txt](prompts/design/decompose.txt)）把设计任务拆成若干验证子任务，落盘 `cache/design_subtasks.json`。
- 复用 `VerificationAgent` 逐一执行子任务，最后用 Pro 模型合并出统一的实验分析。

### 4. ReportAgent &mdash; [src/agents/report_agent.py](src/agents/report_agent.py)

- 通过 [src/utils/docx_outline.py](src/utils/docx_outline.py) 解析指导书 DOCX 章节大纲，把 Markdown 报告结构锁死为指导书原文顺序。
- 生成"实验环境"、"实验目的"等固定章节。
- 按验证性 / 设计性 / 挑战性三类组装正文，嵌入截图与分析，并逐题回答思考题。

所有 LLM 调用统一经 [src/utils/ai_utils.py](src/utils/ai_utils.py) 的 `generate_content_with_tools` / `generate_react_native` 发起，内置 429 / thought-signature 异常退避重试。

---

## 缓存与断点续跑

缓存目录 `cache/`：

```
cache/
├── parsing_result.json        # ContentParsingAgent 的解析结果
├── design_subtasks.json       # DesignAgent 的子任务分解
├── tasks/<task_id>.json       # 每个验证任务的 TaskRecord（含截图路径与分析）
└── llm_python_runs/           # LLM 生成 Python 脚本的执行痕迹
```

**任务粒度的断点续跑**：

- 每个验证 / 设计子任务成功后，其 `TaskRecord` 立即写入 `cache/tasks/<task_id>.json`。
- 再次运行 `uv run main.py` 时，命中缓存的任务会跳过仿真与 LLM 调用，直接复用既有截图与分析。
- 命中缓存的任务还会**复查目标校验**：如果缓存里的截图与分析在新的 judge 下不一致，会被标记失效并自动重跑。
- 如果某个任务在 10 次重试后依然失败，主流程会终止；**修复问题后直接再跑 `uv run main.py`，已完成的任务不会重算，只会重新尝试那些没通过的**。

要从头开始：

```bash
uv run main.py --clear-cache
```

---

## ⚠️ 前置条件：启动 Logisim Headless WebSocket 服务

**在运行主程序前，必须先手动启动 Logisim Headless 后端服务。**
项目地址：<https://github.com/vintcessun/Logisim-ws>

VerificationAgent 与 DesignAgent 均通过 `ws://localhost:9924/ws` 与仿真器通信。若服务未运行，会立即失败：

```
CRITICAL: 无法连接到 ws://localhost:9924/ws。
请手动运行 Logisim Headless 服务器（如：run_backend.bat）！
```

端口号可在 [config/config.toml](config/config.toml) 中修改：

```toml
[headless]
port = 9924
```

---

## 快速开始

### 1. 安装依赖

项目使用 [uv](https://github.com/astral-sh/uv) 管理 Python 环境与依赖：

```bash
uv sync
```

### 2. 配置

```bash
cp config/config-template.toml config/config.toml
```

编辑 [config/config.toml](config/config.toml)：

```toml
[gemini]
api_key     = "YOUR_API_KEY"
base_url    = "https://api.example.com/"   # 留空则使用 Google 官方端点
model_pro   = "gemini-3-pro"
model_flash = "gemini-3-flash-lite"

[headless]
port = 9924
```

### 3. 放入实验资料

将指导书 PDF / DOCX、学生提交的压缩包（含 `.circ`）放入 `data_in/` 目录。

### 4. 启动 Logisim Headless 服务

见上节。

### 5. 运行

```bash
# 正常运行：增量续跑，命中缓存的任务跳过
uv run main.py

# 清空缓存从零开始
uv run main.py --clear-cache
```

**断网 / 崩溃 / Ctrl-C 后**，直接再跑一次 `uv run main.py` 即可从上次断点继续，不需要任何额外参数。

---

## 输出结构

```
output/
├── 实验报告.md                   # 最终 Markdown 报告
├── 实验报告.assets/              # 所有截图
│   ├── reference_<任务名>.png    # 参考电路图
│   └── <任务名>.png              # 验证结果截图
└── 提交电路/
    └── <任务名>.circ             # 归档的学生电路
```

---

## LLM 可用工具集

ReAct 过程中可调用的工具（定义于 [src/utils/tool_definitions.py](src/utils/tool_definitions.py)）：

| 工具 | 说明 |
|---|---|
| `get_io` | 列出当前子电路的输入 / 输出引脚及带标签组件 |
| `get_value` / `set_value` | 读写引脚或组件的当前值 |
| `get_component_info` | 读取组件元数据（位宽、容量等） |
| `load_memory` | 把 `workspace/<filename>.txt` 载入 RAM / ROM（只填文件名）|
| `tick_until` | 驱动 CLK 直到目标引脚达到期望值或超时 |
| `run_until_stable_then_tick` | 等电路稳定后再 tick 一次 |
| `check_value` | 断言某个引脚 / 组件当前值 |
| `get_screenshot` | 抓取仿真器当前画面回传给模型 |
| `switch_circuit` | 切换子电路 |
| `report_verdict` | 模型主动上报 `goal_reached + reason`，终止 ReAct |

---

## 依赖说明

- **Python 3.13+**
- **Logisim Headless** &mdash; 提供 WebSocket API，需自行获取并启动。
- **7-Zip** 或其他 `patool` 支持的解压器 &mdash; 处理 `.rar` / `.7z` 压缩包时需要，需加入系统 `PATH`。

---

## 目录速览

- [main.py](main.py) &mdash; 主流水线入口
- [src/agents/](src/agents) &mdash; 四个智能体
- [src/utils/](src/utils) &mdash; 仿真器、缓存、AI 调用、DOCX 大纲工具
- [prompts/](prompts) &mdash; 各智能体的提示词
- [cache/](cache) &mdash; 运行时缓存（断点续跑依赖）
- [workspace/](workspace) &mdash; 参考电路与测试数据（`.circ` / `.txt`）
- [data_in/](data_in) &mdash; 投喂的指导书与学生压缩包
- [output/](output) &mdash; 最终报告与截图
# autozhy_logisim_report

基于多智能体协作架构的 Logisim 实验全自动化工具。输入压缩包形式的实验资料（PDF 指导书 + `.circ` 电路文件），输出完整 Markdown 实验报告、截图与归档电路，无需任何人工介入。

---

## 智能体流水线

主流程（`main.py`）按顺序衔接四个智能体：

### 1. ContentParsingAgent（`src/agents/content_parsing.py`）

- 递归解压 `data_in/` 下的压缩包，自动处理 ZIP 内中文文件名（GBK 编码）
- 调用 LLM 两阶段解析：**阶段一** 对全文分类识别所有实验模块；**阶段二** 针对验证性实验拆解原子测试用例
- 生成结构化的 `ParsingResult`，包含 `verification_tasks`、`design_tasks` 以及电路/文档路径关联

### 2. VerificationAgent（`src/agents/verification_agent.py`）

- 读取 `prompts/verification/blueprint.txt`，调用 LLM（Pro 模型）将任务描述转换为 Logisim Headless WebSocket API 动作序列（JSON）
- 通过 `LogisimEmulator`（`src/utils/sim_runner.py`）将动作序列逐条发往 WebSocket 服务并执行
- 执行失败时自动进入**最多 9 轮自修复重试**循环，每轮重置仿真器状态、附带失败反馈重新生成动作序列
- 每次执行前由 Flash 模型识别并切换到目标子电路（`prompts/verification/switch.txt`）
- 全量成功后截图保存至 `output/实验报告.assets/`，再调用 Pro 模型生成文字分析

### 3. DesignAgent（`src/agents/design_agent.py`）

- 调用 `LogisimEmulator` 对**参考电路**截图，保存至 `output/实验报告.assets/`
- 将待提交电路拷贝归档到 `output/提交电路/`
- 调用 Flash 模型（`prompts/design/decompose.txt`）将设计任务拆解为若干细粒度验证子任务
- 内部直接调用 `VerificationAgent` 依次执行所有子任务，并用 Pro 模型合并生成统一实验分析

### 4. ReportAgent（`src/agents/report_agent.py`）

- 从 PDF / DOCX 中提取文本，调用 Pro 模型生成**实验环境**、**实验目的**等固定章节
- 识别哪些设计任务属于"挑战性实验"（3.3 节），其余归为 3.2 节
- 汇整所有截图和分析文字，按 3.1 验证性 / 3.2 设计性 / 3.3 挑战性结构组装 Markdown 报告
- 逐题回答实验报告中的思考问题

所有 LLM 调用均经过 `src/utils/ai_utils.py` 的 `generate_content_with_tools` 统一封装，自动挂载全量工具集并开启 Gemini AFC（Automatic Function Calling，最多 64 次）。

---

## ⚠️ 前置条件：启动 Logisim Headless WebSocket 服务

**在运行主程序前，必须先手动启动 Logisim Headless 后端服务。**
[项目地址](https://github.com/vintcessun/Logisim-ws)

VerificationAgent 和 DesignAgent 均通过 `ws://localhost:9924/ws` 与仿真器通信，若服务未运行，所有涉及电路操作的步骤会立即失败并报错：

```
CRITICAL: 无法连接到 ws://localhost:9924/ws。
请手动运行 Logisim Headless 服务器（如：run_backend.bat）！
```

启动方式取决于你使用的 Logisim Headless 版本，通常为：

```bash
# 示例：执行项目附带的启动脚本
run_backend.bat

# 或手动启动 Headless JAR
java -jar logisim-headless.jar --ws-port 9924
```

服务启动成功后，控制台应出现 WebSocket 监听提示。端口号可在 `config/config.toml` 中修改：

```toml
[headless]
port = 9924
```

---

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置

复制并填写配置文件：

```bash
cp config/config-template.toml config/config.toml
```

编辑 `config/config.toml`：

```toml
[gemini]
api_key   = "YOUR_API_KEY"
base_url  = "https://api.example.com/"   # 留空则使用 Google 官方端点
model_pro   = "gemini-3-flash"
model_flash = "gemini-3.1-flash-lite"

[headless]
port = 9924
```

### 3. 放入实验资料

将指导书 PDF、学生提交的压缩包（含 `.circ` 文件）放入 `data_in/` 目录。

### 4. 启动 WebSocket 服务

见上方「前置条件」章节，**必须在第 5 步之前完成**。

### 5. 运行

```bash
# 正常运行（增量，命中缓存的任务会跳过）
uv run main.py

# 清除全部缓存重新开始
uv run main.py --clear-cache
```

---

## 输出结构

```
output/
├── 实验报告.md              # 最终 Markdown 报告
├── 实验报告.assets/         # 所有截图（参考电路图 + 验证结果截图）
│   ├── reference_<任务名>.png
│   └── <任务名>.png
└── 提交电路/                # 归档后的 .circ 文件
    └── <任务名>.circ
```

---

## LLM 可用工具集

所有 LLM 调用均可使用以下工具（定义于 `src/utils/tool_definitions.py`）：

| 工具 | 说明 |
|---|---|
| `tool_inventory_circuit` | 盘点电路工程中所有子电路及组件统计 |
| `tool_get_geometry` | 获取指定组件的端口坐标与位宽 |
| `tool_check_topology` | 提取电路逻辑拓扑（网表） |
| `tool_apply_modifications` | 执行 Python 脚本对电路进行修改 |
| `tool_run_validation` | 结构验证 + WebSocket 行为验证 |
| `tool_write_and_run_python` | 将 Python 代码写入临时文件并执行，返回 stdout/stderr |
| `search_web` | 通过 SearXNG 搜索互联网 |

---

## 依赖说明

- **Python 3.13+**
- **Logisim Headless** — 提供 WebSocket API，项目本身不附带，需自行获取
- **7-Zip** 或其他 patool 支持的解压工具 — 用于处理 `.rar` / `.7z` 格式压缩包，需加入系统 `PATH`
