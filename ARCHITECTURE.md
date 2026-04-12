# 项目架构设计文档

## 1. 架构总览 (Architecture Overview)

本项目是一个高度模块化的**层次型多智能体系统 (Hierarchical MAS)**，专为 Logisim 电路仿真环境设计。

系统通过 **Logisim Headless WebSocket API (v0.1)** 实现了对底层电路引擎的深度掌控。系统由四个核心智能体协同工作，深度融合了 **Google Gemini 2.0 Pro/Flash 模型** 与 **新版 logisim_logic 构建库**。通过 API 级闭环验证与指令驱动，系统实现了从“非结构化实验要求”到“逻辑自洽电路生成”及“标准化实验报告”的全自动闭环。

---

## 2. 四大核心智能体工作流 (Agent Workflow)

系统的核心业务逻辑由四大智能体共同驱动，目前已全面转型为**无头化 (Headless)** 架构：

### 2.1 内容解析智能体 (Content Parsing Agent)
- **职责**：数据摄入、任务识别与上下文构建。
- **执行逻辑**：
  1. 递归处理 `data_in/` 目录，提取 `.circ`、指导书及报告模板。
  2. 使用 LLM 将题目要求转化为结构化的 Task 数据。

### 2.2 设计性实验智能体 (Design Agent)
- **职责**：基于 Pro/Flash 双核架构进行电路拓扑设计与物理构建。
- **核心逻辑**：
  1. **Pro (架构师)**：负责高维度的 CLA 公式规划与分层布线策略设计。
  2. **Flash (执行者)**：基于新版 `logisim_logic` 的 `ProjectFacade` 接口，生成 Python 构建脚本并物理执行。
  3. **自愈循环 (Self-Healing)**：生成的电路通过后端 WebSocket 加载并进行内部数学校验，若逻辑不符，则反馈错误日志驱动 Flash 重新设计，直至 100% 对齐真值表。

### 2.3 验证性实验智能体 (Verification Agent)
- **职责**：指令级仿真驱动、状态观测与结果捕获。
- **核心逻辑 (Headless 架构)**：
  1. **动作转换**：通过 LLM 将验证任务映射为 WebSocket API 指令（`set_value`, `tick_until`, `get_value`）。
  2. **指令执行**：直接与 Logisim 后端通信，无需 GUI 介入。
  3. **视觉分析**：获取无头渲染的电路截图，通过模型进行局部裁剪（Unit-base Cropping）与状态识别。

### 2.4 实验报告智能体 (Report Agent - Orchestrator)
- **职责**：全局调度与最终 Markdown 文档组装。
- **核心逻辑**：
  1. 统筹前置智能体产出的图片与分析报告。
  2. 按照 `docs/examples/` 中的样本格式进行像素级排版对齐。

---

## 3. 提示词分离架构 (Prompt Engineering)

为了提升系统的可维护性，所有智能体均实现了提示词与代码的分离：
- **仓库地址**：`./prompts/`
- **层级架构**：
  - `/design/`: 存放 strategy (Pro) 与 execution (Flash) 指令。
  - `/verification/`: 存放 API 映射指令与图像裁剪逻辑。
  - `/report/`: 存放文字润色与格式填充模板。

---

## 4. 后端交互层 (Backend & Simulation)

系统通过 WebSocket 协议 (Port 9924) 与 Logisim 后端实时交互，取代了旧版的 Win32 GUI 操控。
- **协议优势**：高并发仿真（支持 Virtual Threads）、像素级截图对齐、零延迟状态注入。
- **构建引擎**：使用 `src/logisim_logic` 库进行 `.circ` 的 XML 结构化管理与物理生成。

---

## 5. 项目产出与规范

- **产出路径**：`output/{实验名}.md` 及其关联的 `.assets/` 资源目录。
- **文档标准**：严格锚定 `docs/examples/实验报告.md`，实现对学生真实实验报告的完美复现。
