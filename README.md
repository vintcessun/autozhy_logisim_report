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
