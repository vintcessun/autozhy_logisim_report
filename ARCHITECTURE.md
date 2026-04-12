# 项目架构设计文档

## 1. 架构总览 (Architecture Overview)

本项目是一个高度模块化的**层次型多智能体系统 (Hierarchical MAS)**，专为 Logisim 电路仿真环境设计。

系统通过 **Logisim Headless WebSocket API (v0.1)** 实现了对底层电路引擎的深度掌控。系统环境由 **uv** 包管理工具统一管控，确保环境的一致性与可移植性。系统由四个核心智能体协同工作，深度融合了 **Google Gemini 2.0 Pro/Flash 模型** 与 **新版 logisim_logic 构建库**。通过 API 级闭环验证与指令驱动，系统实现了从“非结构化实验要求”到“逻辑自洽电路生成”及“标准化实验报告”的全自动闭环。

---

## 2. 四大核心智能体工作流 (Agent Workflow)

系统的核心业务逻辑由四大智能体共同驱动，目前已全面转型为**无头化 (Headless)** 架构：

### 2.1 内容解析智能体 (Content Parsing Agent)
- **职责**：数据摄入、任务识别与上下文构建。
- **执行逻辑**：
  1. 递归处理 `data_in/` 目录，提取 `.circ`、指导书及报告模板。
  2. 使用 LLM 将题目要求转化为结构化的 Task 数据。

### 2.2 设计性实验智能体 (Design Agent)
- **职责**：基于 Pro/Flash 双核架构进行**增量式电路设计**。
- **核心逻辑**：
  1. **Pro (架构师)**：负责高维度的电路规划。**增量设计模式**：设计不再从零开始，而是识别模板 `target.circ` 中的预留位置，利用其中已有的子电路（如主电路框架、现成的门电路等）进行逻辑填充。
  2. **Flash (执行者)**：基于新版 `logisim_logic` 的 `ProjectFacade` 接口，通过 `session.edit_circuit()` 定位并对指定子电路进行修改，保持原有接口对齐。
  3. **自愈循环 (Self-Healing)**：生成的电路通过 WebSocket 加载并进行内部校验，若不符则驱动 Flash 修正，直至完全对齐真值表。

### 2.3 验证性实验智能体 (Verification Agent)
- **职责**：指令级仿真驱动、状态观测与结果捕获。
- **核心逻辑 (Headless)**：将验证任务映射为 WebSocket 指令，采集仿真数据与无头渲染截图。

### 2.4 实验报告智能体 (Report Agent - Orchestrator)
- **职责**：全局调度与最终 Markdown 文档组装。

---

## 3. 环境与执行标准

- **包管理**：项目使用 `uv` 运行。所有测试和运行脚本必须使用 `uv run` 前缀。
- **提示词仓库**：统一存放于 `./prompts/`。
- **后端交互**：通过 WebSocket 协议 (Port 9924) 实时交互。

---

## 4. 后端交互层 (Backend & Simulation)

系统通过 WebSocket 协议与 Logisim 后端实时交互。
- **构建引擎**：使用 `src/logisim_logic` 库进行 `.circ` 的增量编辑。
