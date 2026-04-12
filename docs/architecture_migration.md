# 架构迁移记录：从视觉自动化到 Headless API，从 pip 到 uv

## 1. 交互层变迁 (Visual to Headless)

早期的 `autozhy_logisim_report` 依赖于 `ui-tars` 模型与 `PyAutoGUI` 模拟鼠标点击。虽然具备极高的通用型，但在性能和稳定性上存在瓶颈。

### 1.1 核心转变
- **协议**：引入了基于 WebSocket 的 Logisim Headless API。
- **效率**：支持异步仿真任务，验证耗时从分钟级降至秒级。
- **稳定性**：消除了物理像素对齐、窗口焦点丢失等 GUI 常见故障。

## 2. 环境管理进化 (pip/conda to uv)

项目已全面从传统的 `pip` 管理转向 `uv`。
- **性能**：`uv` 带来的极速解析与并行下载显著缩短了环境搭建时间。
- **一致性**：通过 `uv.lock` 确保了各节点运行环境的像素级对齐。
- **指令规范**：所有终端命令（测试、运行、安装）必须使用 `uv` 前缀。
- **执行示例**：
  - 安装依赖：`uv sync`
  - 运行脚本：`uv run python main.py`
  - 运行测试：`uv run pytest tests/`

## 3. 设计逻辑演进 (Scratch to Incremental)

从“白手起家”从零构建 XML，转向了基于库的“增量编辑”模式。
- **工作流**：载入老师提供的 `target.circ` -> 识别预留位置 -> 精准修改逻辑。
- **库支持**：使用 `src/logisim_logic` 提供的 `ProjectFacade` 实现对现有子电路的安全编辑。
