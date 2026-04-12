# 数据流转管线 (Headless & Incremental 架构版)

本文件定义了 `autozhy_logisim_report` 系统内部的数据转换逻辑、XML 语义解析标准以及从电路组件到实验报告章节的自动化映射算法。

## 1. 原始输入层：增量式解析

系统通过解析 `.circ` 文件的 XML 树来构建电路的逻辑拓扑。与旧版全盘重建不同，新版重点识别模板中的“预留槽位”。

### 1.1 关键组件识别逻辑
- **Subcircuits**: 识别哪些子电路已由老师预设（如名为 `CLA16`, `Adder` 等的空壳电路）。
- **Pins & Tunnels**: 提取已有引脚的 Label 和坐标，以此作为增量设计的锚点。

## 2. 设计管线：增量构建流程

基于 `uv run` 环境，执行以下流程：

1. **模板载入**：`DesignAgent` 载入 `target.circ` 并注入 Pro/Flash 协作上下文。
2. **增量编辑**：Flash 模型使用 `ProjectFacade.edit_circuit()` 锁定目标子电路。
3. **连线自愈**：
   - 物理生成临时文件。
   - 通过 WebSocket 反馈真实的仿真逻辑状态（不再是单纯的 XML 静态检查）。
   - 真值表对标：若逻辑不符，自动生成 Diff 报告驱动 Flash 重新连线。

## 3. 验证管线：Headless 仿真

指令流完全基于 WebSocket：
1. `load_circuit {target_path}`
2. `set_value` -> `tick_until` -> `get_value`
3. `get_screenshot` -> PIL 裁剪 -> 图像分析。

## 4. 汇总管线

1. **自动章节定位**：根据识别到的子电路 ID 与任务类型进行映射。
2. **报告生成**：使用 `uv run` 驱动最终的 Markdown 渲染。
