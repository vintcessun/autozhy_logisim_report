# 开发指南

本文件定义了 `autozhy_logisim_report` 项目的工程标准、环境配置流及核心开发约束。

## 1. 运行时环境 (Runtime)

- **Python 版本**: `3.12+` (严格遵循根目录 `.python-version`)。
- **操作系统**: 推荐 **Windows 10/11**。
  - *原因*: Logisim-evolution 的 GUI 渲染与跨平台窗口管理库在 Windows 环境下最为稳定。
- **硬件要求**: 必须配备支持 **CUDA** 的 NVIDIA 显卡（建议显存 $\ge$ 8GB），用于本地运行 `ui-tars` 推理。

------

## 2. 核心库选型 (Core Libs)

| **依赖库**                | **用途**       | **选型原因**                                               |
| ------------------------- | -------------- | ---------------------------------------------------------- |
| **`uv`**                  | 项目与依赖管理 | 极速的并行包管理，通过 `uv.lock` 确保 Agent 环境的确定性。 |
| **`pygetwindow`**         | 跨平台窗口管理 | 负责定位、激活、置顶和调整 Logisim 窗口。                  |
| **`ui-tars`**             | GUI 视觉解析   | 2026 SOTA 级本地 VLM，提供像素级物理坐标定位。             |
| **`google-generativeai`** | 核心大脑       | 调用 `gemini-3.1-pro` (思考) 与 `gemini-3-flash` (杂活)。  |
| **`pyautogui`**           | 模拟执行       | 物理机键鼠模拟，配合物理防暴走机制。                       |
| **`pydantic`**            | 数据建模       | 强类型校验 Agent 间流转的 JSON 协议与电路拓扑结构。        |

------

## 3. 编程与命名规范 (Coding Standards)

请严格遵循以下命名风格，以确保代码的统一性：

- **变量名与函数名**: 使用 `snake_case` (小写加下划线)。

  - *示例*: `screen_lock`, `get_circuit_path()`, `is_simulation_finished`。

- **类名**: 使用 `PascalCase` (大写驼峰)。

  - *示例*: `class EnableCard:`, `class ContentParser:`, `class DesignCritic:`.

- **DPI 意识声明**: 所有 GUI 交互入口必须强制执行，防止坐标偏移：

  Python

  ```
  import ctypes
  ctypes.windll.shcore.SetProcessDpiAwareness(2) 
  ```

------

## 4. 核心设计约束 (Engineering Constraints)

### 4.1 异构模型路由

- **`gemini-3.1-pro`**: 负责高维度思考。处理设计实验的 Critic 逻辑、验证实验的异常决策、报告的深度上下文精简。
- **`gemini-3-flash`**: 负责流程性杂活。包括报告排版、PDF/DOCX 文本初步整理。
- **`gemma4` (本地)**: 本地代码逻辑辅助、XML 格式化、轻量级文本清洗。
- **`ui-tars` (本地)**: 纯视觉解析。

### 4.2 设计性实验：静态可达性验证

在 `Design Experiment Agent` 闭环中，XML 修改后必须经过 Python 脚本的静态验证：

- **可达性算法**: 建立电路拓扑图，确保每一条 `wire` 的双端均连接在有效的组件引脚上。
- **闭环约束**: **严禁生成空线或无效连线**。未通过验证的 XML 会被拦截并反馈给模型重推。

### 4.3 物理资源锁

- 涉及窗口置顶及 `pyautogui` 点击的操作，必须包裹在 `asyncio.Lock()` 实现的 `screen_lock` 临界区内。

------

## 5. 环境配置与安装 (Environment Setup)

推荐使用 `uv add` 管理项目依赖，确保所有开发者环境同步。

### 5.1 项目初始化

Bash

```
# 初始化环境并同步 pyproject.toml 中的依赖
uv sync
```

### 5.2 安装/添加 核心依赖

Bash

```
# 使用 uv add 将依赖记录到项目配置文件中
uv add google-generativeai pyautogui pygetwindow pydantic click ui-tars transformers accelerate
```

### 5.3 强制替换 GPU 版 PyTorch (重要)

由于 `ui-tars` 在 CPU 下推理延迟极大，必须手动通过 `uv pip`（仅限此特殊覆盖步骤）或在 `pyproject.toml` 中配置 source 来安装 CUDA 版本：

Bash

```
# 强制替换为 GPU (CUDA 12.1) 版本
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121 --force-reinstall
```

------

## 6. 调试与输出 (Debugging & Output)

所有非正式产物统一收拢至 `./debug/` 目录下：

- **截图调试**: `./debug/screenshot/` (记录 ui-tars 识别与点击轨迹)。
- **中间件调试**: 所有的 Log、中间生成的 XML 片段及临时文件均存放于 `./debug/`。
- **正式产物**: 仅存放在 `output/` 中，包含 `{name}.md` 和 `{name}.assets/`。

------

## 7. 安全机制 (Fail-Safe)

- `pyautogui.FAILSAFE = True` 必须保持开启。
- **紧急终止**: 运行中将鼠标快速移动至屏幕 **左上角顶点**，可瞬间强制停止所有进程。

