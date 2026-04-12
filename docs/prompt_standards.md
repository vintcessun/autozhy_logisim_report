# 提示词分离标准与规范 (Incremental & UV 版)

## 1. 设计理念

本作核心在于“逻辑解耦”：即电路构建的 Python 代码逻辑是通用的，而具体的“设计策略”与“指令映射”则通过外部文本文件定义。

## 2. 目录结构

所有提示词存放于 `./prompts/`：
- `/design/`: 
  - `strategy.txt`: Pro 架构师设计策略（核心：**模板子电路识别**）。
  - `execution.txt`: Flash 执行者实现脚本（核心：**增量连线逻辑**）。
- `/verification/`:
  - `blueprint.txt`: API 动作蓝图生成。
  - `cropping.txt`: 图像单元化裁剪映射。

## 3. 标准格式

提示词支持以下元数据占位符：
- `{{goal}}`: 实验具体要求（来自 `info.txt`）。
- `{{context}}`: 模板电路的逻辑快照（由 `logisim_logic` 的 `extract_logical_circuit` 生成）。
- `{{spec}}`: Pro 产出的规格说明。
- `{{template_path}}`: 供 Flash 显式加载的模板路径。

## 4. 维护规范

1. **绝对性约束**：强制模型使用 `session.edit_circuit()` 而非新建。
2. **错误反馈**：在自愈循环中，系统会将 `internal_verifier` 的报错日志注入 `{{feedback}}` 占位符供 Flash 修正。
