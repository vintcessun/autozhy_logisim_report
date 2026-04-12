# 架构迁移记录：从视觉自动化到 Headless API

## 1. 迁移背景

早期的 `autozhy_logisim_report` 依赖于 `ui-tars` 模型与 `PyAutoGUI` 模拟鼠标点击。虽然具备极高的通用型，但在 Logisim 仿真场景下暴露了以下问题：
- **稳定性差**：GUI 弹窗、窗口焦点丢失、DPI 缩放不一致常导致点击偏移。
- **仿真速度慢**：时钟驱动需要模拟物理点击，对于 8-bit 乘法器这类需要多周期的电路效率极低。
- **资源占用高**：本地运行视觉模型对 GPU 资源消耗巨大。

## 2. 核心方案：Logisim Headless WebSocket API (v0.1)

于 2026-04 引入。该方案彻底改变了系统与 Logisim 的交互方式：

### 2.1 高性能仿真引擎
- **接口**：WebSocket `ws://localhost:9924/ws`。
- **特性**：支持 `Virtual Threads`，允许在一个物理进程中并行处理多个仿真会话。
- **原子操作**：提供了 `set_value`、`get_value` 与 `tick_until`（带时钟脉冲注入），实现了 100% 的仿真正确率。

### 2.2 无头渲染与截图
- **Server Side Rendering**：使用 Java `Graphics2D` 直接在后端渲染电路图并返回 PNG 字节流。
- **Unit-base Cropping**：前端 Agent 结合 LLM 实现对渲染结果的智能裁剪，精准提取探针、数码管等关键区域。

## 3. 设计闭环的进化

从单纯的 XML 文字拼凑，转向了基于 `src/logisim_logic` 构建库的脚本化生成。
- **新工作流**：Pro 规划负载 -> Flash 实现 Python 脚本 -> 物理生成 `.circ` -> WebSocket 载入 -> 自动化真值表校验报告 -> 自我修正。

## 4. 结论

此次迁移显著提升了系统的执行确定性（Determinism），将 16 位加法器的验证耗时从分钟级降低到了秒级。
