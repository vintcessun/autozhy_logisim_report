# ADR 0001: 智能体角色定义与增量设计转型

## 1. 内容解析智能体 (Content Parsing Agent)

**职责定位**：流水线的“传感器”。基于 `uv` 运行。

- **核心动作**：解析 `target.circ` 模板，标记出其中的空壳子电路 (Stub Circuits)。
- **任务构造**：将实验要求、思考题与具体的子电路占位符进行关联。

## 2. 设计性实验智能体 (Design Agent)

**职责定位**：核心“构建者”。采用 **Pro/Flash 协作架构** 与 **增量编辑模式**。

- **Pro (Strategy)**：分析模板提供的原子门电路（如 `FA1`）和预留槽位（如 `CLA16`），制定组装策略。
- **Flash (Execution)**：通过 `session.edit_circuit()` 将逻辑“注入”模板，而不是从头生成新的项目结构。
- **自愈循环**：基于 WebSocket 仿真结果进行反馈纠错。

## 3. 验证性实验智能体 (Verification Agent)

**职责定位**：自动化“质检员”。基于 **WebSocket API**。

- **去视觉化驱动**：通过 API 精准操作引脚，代替不稳定的 GUI 视觉识别。
- **单元化裁剪**：获取全局截图后，利用 LLM 进行 Unit-base 裁剪，确保存档图片的紧凑度。

## 4. 实验报告智能体 (Report Agent - Orchestrator)

**职责定位**：总指挥与“文字润色师”。

- **排版对齐**：使用 `uv run` 驱动最终的 Markdown 合成。