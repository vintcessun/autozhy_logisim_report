# ADR 0001: 智能体角色定义与 Headless 转型

## 1. 内容解析智能体 (Content Parsing Agent)

**职责定位**：流水线的“传感器”。处理非结构化和半结构化的输入数据。

- **核心动作**：递归解压 `data_in/`，提取文稿、PDF 与 `.circ`。
- **知识提取**：利用 `pdfplumber` 获取环境与目的，构造结构化任务清单 (JSON)。

## 2. 设计性实验智能体 (Design Agent)

**职责定位**：核心“构建者”。采用 **Pro/Flash 协作架构** 与 **Headless 仿真闭环**。

- **Pro (Strategy)**：基于 `ProjectFacade` 制定高层修改/构建策略，确保 CLA 等复杂逻辑的布线模型正确。
- **Flash (Execution)**：编写符合 `src/logisim_logic` 规范的 Python 脚本。
- **仿真反馈**：不再依赖 GUI 视觉纠错，而是通过 WebSocket 将结果加载至后端，执行 `internal_verifier` 进行数学真值对撞。

## 3. 验证性实验智能体 (Verification Agent)

**职责定位**：自动化“质检员”。彻底转型为 **WebSocket API 驱动者**。

- **API 蓝图 (Blueprint)**：根据 NL 任务映射为 `set_value`/`tick_until` 指令序列。
- **无头取证**：通过 `get_screenshot` 获取 PNG 字节流，辅以 LLM 进行单元化裁剪 (Unit-base cropping)，仅保留关键输出区域（如数码管）。
- **去视觉化**：彻底移除对 `ui-tars` 模拟鼠标的依赖，提升仿真速度与稳定性。

## 4. 实验报告智能体 (Report Agent - Orchestrator)

**职责定位**：总指挥与“文字润色师”。

- **排版对齐**：严格控制 Markdown 输出结构，确保符合样本实验报告的排版标准。
- **语态模拟**：将冗长的仿真日志改写为流畅的学生实验分析。