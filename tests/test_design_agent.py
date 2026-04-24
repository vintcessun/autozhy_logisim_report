import sys
import os
import pytest
import shutil
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# 确保项目根目录在导入路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.design_agent import DesignAgent
from src.core.models import TaskRecord

CASE_DIR = Path("tests/cases/design")


def make_task() -> TaskRecord:
    """从 info.txt 构造 TaskRecord"""
    info_path = CASE_DIR / "info.txt"
    info = (
        info_path.read_text(encoding="utf-8").strip()
        if info_path.exists()
        else "测试设计任务"
    )
    return TaskRecord(
        task_name="16位快速加法器（组内并行、组间并行）",
        task_type="design",
        analysis_raw=info,
        section_text=info,
        source_circ=[str(CASE_DIR / "target.circ")],
        reference_circ=str(CASE_DIR / "reference.circ"),
    )


# ── Mock 测试（无外部服务）──────────────────────────────


@pytest.mark.asyncio
async def test_copy_target_circuit(tmp_path):
    """验证 _copy_target_circuit 应将 target.circ 拷贝到 output/提交电路/{name}.circ"""
    mock_client = MagicMock()
    mock_config = MagicMock()
    agent = DesignAgent(mock_client, mock_config, "gemini-flash")

    # 配置临时 output 目录
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        task = make_task()
        # 创建模拟源文件
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        src_file = source_dir / "target.circ"
        src_file.write_text("dummy circuit content")

        agent._copy_target_circuit(task, src_file)

        dest = tmp_path / "output" / "提交电路" / f"{task.task_name}.circ"
        assert dest.exists()
        assert dest.read_text() == "dummy circuit content"
        # 比较绝对路径以避免不一致
        assert Path(task.source_circ[0]).resolve() == dest.resolve()
    finally:
        os.chdir(old_cwd)


@pytest.mark.asyncio
async def test_decompose_returns_task_list_mock():
    """Mock Flash LLM 返回，验证返回 list[TaskRecord] 且每项含 task_name"""
    mock_client = MagicMock()
    mock_config = MagicMock()

    mock_response = MagicMock()
    mock_response.text = """[
        {"task_name": "test - ①正+正=正", "description": "X=1,Y=1 期望 S=2", "input_params": {}, "expected": {}},
        {"task_name": "test - ②正+正=负(溢出)", "description": "X=7,Y=1 期望 OF=1", "input_params": {}, "expected": {}}
    ]"""

    agent = DesignAgent(mock_client, mock_config, "gemini-flash")
    task = make_task()

    with patch(
        "src.agents.design_agent.generate_content_with_tools",
        AsyncMock(return_value=mock_response),
    ):
        sub_tasks = await agent._decompose_to_subtasks(task)

    assert isinstance(sub_tasks, list)
    assert len(sub_tasks) == 2
    assert all(isinstance(t, TaskRecord) for t in sub_tasks)
    assert all(t.task_type == "verification" for t in sub_tasks)
    assert sub_tasks[0].task_name == "test - ①正+正=正"


@pytest.mark.asyncio
async def test_screenshot_raises_on_no_emulator():
    """当 Logisim WebSocket 未运行时，_screenshot_reference 应 raise RuntimeError"""
    mock_client = MagicMock()
    mock_config = MagicMock()
    mock_config.headless.port = 9924

    agent = DesignAgent(mock_client, mock_config, "gemini-flash")
    task = make_task()
    ref_path = CASE_DIR / "reference.circ"

    # 模拟 LogisimEmulator 失败
    with patch("src.agents.design_agent.LogisimEmulator") as MockEmulator:
        instance = MockEmulator.return_value
        instance.launch_and_initialize = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="Logisim WebSocket 服务未运行"):
            await agent._screenshot_reference(task, ref_path)


# ── 集成测试（需 config.toml + Logisim WebSocket 服务）───────


@pytest.mark.asyncio
async def test_design_agent_integrated():
    """
    端到端集成测试：
    输入：tests/cases/design/ 的文件
    断言：
      1. sub_tasks 列表非空
      2. output/提交电路/ 下存在命好名的电路文件
      3. output/实验报告.assets/ 下存在参考截图（若服务可用）
    """
    from src.utils.config_loader import ConfigManager
    from src.utils.llm_client import create_genai_client

    config_path = Path("config/config.toml")
    if not config_path.exists():
        pytest.skip("跳过集成测试：未找到 config/config.toml")

    app_config = ConfigManager.load_config(config_path)
    client = create_genai_client(
        api_key=app_config.gemini.api_key,
        base_url=app_config.gemini.base_url,
    )

    agent = DesignAgent(client, app_config, app_config.gemini.model_flash)
    task = make_task()

    # 确保源文件存在
    source_circ = CASE_DIR / "target.circ"
    ref_circ = CASE_DIR / "reference.circ"
    if not source_circ.exists() or not ref_circ.exists():
        pytest.skip("测试素材缺失")

    try:
        updated_task, sub_tasks = await agent.run(task, source_circ, ref_circ)

        print(f"\n[Test] 拆解出 {len(sub_tasks)} 个子任务")
        assert len(sub_tasks) > 0

        circ_dest = Path("output") / "提交电路" / f"{task.task_name}.circ"
        assert circ_dest.exists()

        if updated_task.assets:
            ref_img = Path(updated_task.assets[0])
            assert ref_img.exists()
            assert "reference_" in ref_img.name
    except RuntimeError as e:
        if "Logisim WebSocket 服务未运行" in str(e):
            print("\n[Test] 跳过截图验证，因 Logisim 服务未启动。")
            # 如果截图失败由于服务未运行，我们在此捕获以能测试其他逻辑
            # 但按要求设计是失败的
            raise e
        else:
            raise e
