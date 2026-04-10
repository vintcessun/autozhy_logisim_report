# 技术规格说明书 (SPECIFICATIONS.md)

## 1. 智能体功能规格 (Agent Functional Specs)

### 1.1 内容解析智能体 (Content Parsing Agent)

- **输入**：`data_in/` 目录下的原始压缩包、PDF 指导书、DOCX 报告样本。
- **核心动作**：
  - **递归解压**：解压深度不限，直至提取出所有 `.circ`。
  - **文本路由**：利用 `gemini-3-flash` 将提取的文本分类为“实验环境”、“目的”、“验证要求”和“思考题”。
- **输出**：结构化的 `task_list.json`。

### 1.2 设计性实验智能体 (Design Experiment Agent)

- **输入**：参考电路 XML、设计需求。
- **设计闭环 (Actor-Critic)**：
  1. **XML 生成**：`gemini-3.1-pro` 输出 `.circ` 的代码补丁。
  2. **可达性校验 (Static)**：通过 Python 脚本遍历 XML 树，验证所有 `wire` 标签的 `from` 和 `to` 坐标必须落在 `comp` 或 `wire` 的连接点上。**此过程不消耗视觉 Token。**
  3. **冲突修正**：若校验失败，将错误拓扑反馈给模型重推。
- **输出**：通过校验的电路文件、电路设计全景图（截图）。

### 1.3 验证性实验智能体 (Verification Experiment Agent)

- **输入**：待测 `.circ` 文件、测试向量序列。
- **核心动作**：
  - **视觉对齐**：调用 `ui-tars` 识别组件坐标。
  - **时钟驱动**：模拟点击 CLK 引脚，支持基于状态感知的动态步进（非硬编码 10 次，由大模型判断是否停止）。
  - **结果捕获**：仅截取输出探针/数码管的局部画面。
- **输出**：实验结果局部截图、原始仿真数据。

### 1.4 实验报告智能体 (Report Writing Agent)

- **输入**：前置智能体产出的图片路径、分析数据、思考题。
- **核心动作**：
  - **精简润色**：利用 `gemini-3-flash` 将详细的实验分析压缩为符合学生语气的报告文本。
  - **模板填充**：按照 `docs/examples/实验报告.md` 的层级结构进行 Markdown 组装。
- **输出**：`output/{name}.md`。

------

## 2. 通信协议 (Communication Protocols)

智能体之间通过 `TaskRecord` 对象（Pydantic 模型）进行状态传递，核心字段定义如下：

```python
class TaskRecord(BaseModel):
    task_id: str            # uuid
    task_type: Literal["verification", "design", "challenge"]
    source_circ: list[str]  # 源码绝对路径
    status: str             # pending, executing, finished, failed
    assets: list[str]       # 关联的截图路径 (relative to .assets/)
    analysis_raw: str       # 原始分析文本
    logic_check_pass: bool  # 拓扑可达性状态
```

------

## 3. 硬件交互与并发规格 (Hardware Interaction)

- **窗口管理**：
  - 调用 `pygetwindow` 实例的 `activate()` 和 `maximize()`。
  - **强制前台**：若窗口未响应，通过模拟键盘 `Alt` 键释放系统焦点锁定。
- **屏幕资源锁 (Screen Mutex)**：
  - 变量名：`screen_lock = asyncio.Lock()`。
  - 作用域：涵盖从 `win.activate()` 开始到截图保存结束的全过程。
- **DPI 补偿**：
  - 所有由 `ui-tars` 返回的坐标 $(x, y)$ 在传递给 `pyautogui` 前，需根据 `ctypes` 获取的当前屏幕 Scaling Factor 进行缩放对齐。

------

## 4. 视觉与模型规格 (Model & Vision Specs)

| **模型**           | **角色**     | **输入规格**        | **输出规格**                |
| ------------------ | ------------ | ------------------- | --------------------------- |
| **Gemini 3.1 Pro** | 首席思考官   | 多模态 (全景图/XML) | 复杂设计指令/逻辑分析文本   |
| **Gemini 3 Flash** | 报告速写员   | 文本/局部截图       | Markdown 段落/结构化 JSON   |
| **UI-TARS 1.5**    | 视觉定位     | 1080P/2K 截图       | 归一化点击坐标 $[x, y]$     |
| **Gemma 4**        | 本地逻辑辅助 | XML 代码            | 修复后的 XML / 拓扑校验日志 |

------

## 5. 错误处理与容错 (Error Handling)

1. **可达性死循环**：若 `Design Agent` 连续 5 次未通过静态拓扑校验，触发 `gemini-3.1-pro` 重新审视原始参考电路。
2. **点击未命中**：执行动作后通过截图对比（Image Diff），若界面状态未发生预期变化，重新调用视觉模型进行坐标二次对齐。
3. **Logisim 崩溃**：若检测到 `subprocess` 退出码异常，自动重启仿真器并加载最近一次保存的临时文件。

------

## 6. 文件系统标准 (File System Standard)

### 6.1 目录架构

Plaintext

```
autozhy_logisim_report/
├── debug/                  # 运行时调试
│   └── screenshot/         # 存放 ui-tars 识别过程图
├── data_in/                # 原始输入包
├── output/                 # 成果输出
│   ├── {name}.md
│   └── {name}.assets/      # 仅存最终有效图
└── workspace/              # 临时仿真文件 (.circ)
```

### 6.2 命名约定

- **变量/函数**：`snake_case`（如 `capture_result_image`）。
- **类名**：`PascalCase`（如 `class EnableCard`）。
- **图片命名**：`image-{timestamp}.png`。

