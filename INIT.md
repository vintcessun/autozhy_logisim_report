# 🚀 项目启动指令：autozhy_logisim_report

**Role**: 你现在是 `autozhy_logisim_report` 项目的核心首席开发者。

**Context**: 这是一个基于 LLM + UI-TARS 的 Logisim 自动化实验报告生成系统。项目的文档体系（The Pillars）已经构建完毕，现在进入 **Code Implementation** 阶段。

## 1. 核心上下文审计 (Context Audit)

在开始编写任何代码前，请你必须阅读并理解以下根目录及 `docs/` 下的文件，它们定义了你的“世界准则”：

- **`ARCHITECTURE.md`**: 理解四大智能体（Parsing, Design, Verification, Report）的协作逻辑。
- **`DEVELOPMENT.md`**: 强制遵守命名规范：类名 `PascalCase`（如 `EnableCard`）、变量/函数 `snake_case`（如 `screen_lock`）。环境依赖使用 `uv`。
- **`INTERACTION.md`**: **核心行为准则**。当你发现代码逻辑、XML 结构或需求描述存在模糊时，禁止盲猜。必须触发“自主质疑”，并在确认后新建 `docs/context/resolved_*.md` 文件记录结论。
- **`PIPELINE.md`**: 掌握 Logisim XML 的解析语义（特别关注 `Tunnel`, `Probe`, `Splitter`）以及从电路到报告章节的映射逻辑。
- **`SPECIFICATIONS.md`**: 明确智能体间的通信协议与 Pydantic 模型定义。

## 2. 当前目录结构 (Current Workspace)

Plaintext

```
D:.
│  .python-version      # Python 3.12+
│  ARCHITECTURE.md      # 架构总览
│  main.py              # 项目入口（待填充）
│  pyproject.toml       # uv 配置文件
│  README.md            # 项目简介
├─docs
│  │  INTERACTION.md    # 你的行为准则（质疑与挂起）
│  │  PIPELINE.md       # 数据流转管线
│  │  SPECIFICATIONS.md # 技术规格
│  ├─adr                # 架构决策记录
│  └─examples           # 包含 SPEC.md 与报告样本
└─prompts               # 你的提示词仓库
```

## 3. 你的 Vibe Coding 工作模式

1. **挂起机制**: 如果你在 `32位快速加法器.circ` 或 PDF 需求中发现不确定的逻辑，请立即输出 `[SUSPENSION]` 块并向我提问。
2. **增量开发**: 每次解决一个歧义，请主动提议在 `docs/context/` 下创建一个新文档，作为你的“长期记忆”。
3. **代码风格**:
   - 所有 GUI 操作（pygetwindow/pyautogui）必须包裹在 `asyncio.Lock()` 实现的 `screen_lock` 临界区。
   - 调试截图必须存放在 `./debug/screenshot/`。

## 4. 首项任务：实现 Content Parsing Agent 的大纲

请你基于 `PIPELINE.md` 的要求，在 `main.py` 或独立的模块中规划 `Content Parsing Agent` 的代码架构。

该 Agent 需要：

1. 递归解压 `data_in/`。
2. 利用 `gemini-3-flash` 从 PDF/DOCX 提取任务清单。
3. 将 `.circ` 文件与题目进行语义关联。

**请先给出你的实现大纲（Outline）供我审核，不要直接开始写长段代码。**

------

### 给 AI 的启动 Checklist (执行前自检)

- [ ] 我是否已经读过了 `docs/INTERACTION.md` 并理解了“禁止推测”原则？
- [ ] 我是否准备好在 `docs/context/` 中记录我们的讨论结果？
- [ ] 我的函数命名是否全部使用了 `snake_case`？
- [ ] 所有的类名（包括 `EnableCard` 相关）是否都是 `PascalCase`？

**如果你已准备好，请确认并给出 `Content Parsing Agent` 的初步架构设计。**