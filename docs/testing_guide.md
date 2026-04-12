# 测试指南 (Testing Guide)

## 1. 测试体系总览

本项目采用分层测试策略，涵盖了从原子 API 验证到端到端设计闭环的验证。

## 2. 仿真引擎测试 (Simulation Tests)

用于验证 Logisim Headless WebSocket API 的集成稳定性。

| **测试脚本** | **验证目标** | **运行方式** |
| :--- | :--- | :--- |
| `tests/test_simulator_test1.py` | 验证 16 位加法器在 Headless 模式下的正确性。 | `uv run pytest tests/test_simulator_test1.py` |
| `tests/test_simulator_test2.py` | 验证 8 位乘法器（带时钟脉冲注入）的自动化验证流程。 | `uv run pytest tests/test_simulator_test2.py` |

## 3. 设计智能体测试 (Design Agent Tests)

用于验证 `DesignAgent` 的 Pro/Flash 协作、代码生成及自愈能力。

### 3.1 冒烟测试 (Smoke Test)
- **脚本**：`tests/test_design_smoketest.py`
- **内容**：设计一个极简的 AND 门电路。
- **目的**：打通从 API 请求、物理构建到文件保存的最小全路径。
- **运行**：`uv run python tests/test_design_smoketest.py`

### 3.2 集成逻辑测试 (Integration Logic Test)
- **脚本**：`tests/test_design_integration_logic.py`
- **内容**：要求 Agent 设计一个 16 位先行进位加法器 (CLA)。
- **目的**：验证 Agent 在复杂电路设计中的稳定性，并通过与 Oracle 电路（预期结果）进行黑盒对撞校验。
- **运行**：`uv run pytest tests/test_design_integration_logic.py`

## 4. 调试建议

- **查看日志**：设计过程中的详细推理逻辑记录在 `synthesis_log.txt` 中。
- **中间产物**：临时生成的错误电路会保存在 `workspace/` 目录下（如 `temp_design_0.circ`），可手动打开复核 Flash 模型的连线错误。
- **视觉反馈**：验证过程中的截图保存在 `output/{task_name}.assets/` 中。
