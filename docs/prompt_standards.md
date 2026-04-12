# 提示词分离标准与规范 (Prompt Standards)

## 1. 设计理念

为了实现“逻辑代码不动，业务指令先行”，本项目将所有 Agent 的核心提示词从 Python 代码中抽离。这使得开发者可以通过修改文本文件来微调 Agent 行为，而无需担心引入语法错误。

## 2. 目录结构

所有提示词存放于根目录下的 `prompts/` 文件夹：

Plaintext

```
prompts/
├── design/
│   ├── strategy.txt    # Pro 模型：架构设计策略
│   └── execution.txt   # Flash 模型：Python 实现脚本生成
└── verification/
    ├── blueprint.txt   # WebSocket API 动作蓝图生成
    └── cropping.txt    # 单位化图像裁剪单元选择
```

## 3. 标准格式与占位符

提示词文件使用 Python 风格的 `str.format()` 或简单的字符串替换注入上下文。

### 3.1 常用占位符定义
- `{{goal}}`: 原始任务要求描述。
- `{{context}}`: 电路现状解析（组件清单、拓扑结构）。
- `{{spec}}`: 架构师 (Pro) 产出的规格说明书（传递给执行者）。
- `{{api_spec}}`: 库 API 的核心约束。
- `{{io_info}}`: 电路可用引脚的 JSON 列表。

## 4. 维护规范

1. **绝对性约束**：强制规则应使用 “### 强制规则” 标题，并用序号列出。
2. **示例驱动**：当需要模型输出特定格式（如 JSON 或 Python 代码块）时，必须包含一个最小可运行示例。
3. **分层加载**：Agent 在运行时应首先加载通用 Base Prompt，再根据任务类型拼装对应的子提示词。
