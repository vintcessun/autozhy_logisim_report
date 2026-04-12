# 数据流转管线 (Headless 架构版)

本文件定义了 `autozhy_logisim_report` 系统内部的数据转换逻辑、XML 语义解析标准以及从电路组件到实验报告章节的自动化映射算法。

## 1. 原始输入层：Logisim XML (.circ) 深度解析

系统通过解析 `.circ` 文件的 XML 树来构建电路的逻辑拓扑。

### 1.1 关键组件提取清单 (基于 WebSocket 观测)

- **Input/Output Pins**: 识别 Label 用于仿真激励映射。
- **Tunnel/Splitter**: 用于构建内部逻辑网，判断电路状态。
- **Probe/LED**: 作为仿真结果的关键采集点。

## 2. 设计闭环：Pro/Flash 物理构建管线

### 2.1 架构设计 (Pro)
- 读取任务要求与电路模板。
- 生成设计规格说明书（PDF/DOCX 语义提取）。

### 2.2 代码执行 (Flash)
- 直接调用 `src/logisim_logic` 的 `ProjectFacade` 接口。
- **逻辑闭环**：生成的脚本物理执行后，生成的 `.circ` 文件将通过 WebSocket 发送至后端。
- **反馈链路**：后端执行 `tick_until` 逻辑提取输出值，若不满足数学真值表，则将错误差异反馈给 Flash 进行自我修正。

## 3. 验证管线：Headless 仿真驱动

不再依赖 GUI 视觉定位，全面转向 API 指令流：

1. **载入**：`load_circuit {path}`。
2. **初始化**：`set_value` 注入复位或初值。
3. **仿真**：通过 `tick_until` 或 `step` 驱动时钟引脚。
4. **观测**：通过 `get_value` 轮询关键探针（Probe）状态。
5. **截图**：调用 `get_screenshot` 获取 PNG 字节流，随后进行 LLM 裁剪。

## 4. 汇总与排版管线

1. **章节映射**：根据 `TaskRecord` 中的组件特征（如 `FA`, `ROM`），自动匹配模板中的 `## 3.1` 等章节。
2. **图片引用**：将 `.assets/` 中的截图以相对路径插入 Markdown。
3. **文本润色**：使用 `gemini-3-flash` 模拟学生口吻补全实验分析。

## 5. 提示词分离架构

所有阶段的指令均从 `./prompts/` 目录动态加载：
- `/parsing/`: 任务拆解。
- `/design/`: 策略与执行指令。
- `/verification/`: API 动作蓝图与裁剪逻辑。
- `/report/`: 报告组装模板。
