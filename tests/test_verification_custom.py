import asyncio
import pytest
from pathlib import Path
import shutil
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.config_loader import ConfigManager
from src.utils.llm_client import create_genai_client
from src.agents.verification_agent import VerificationAgent
from src.core.models import TaskRecord


@pytest.mark.asyncio
async def test_verification_case_sandbox():
    """专家级验证沙盒：输入 target.circ/info.txt，产出 output.png/output.txt"""
    # 1. 环境准备
    base_dir = Path("tests/cases/verification")
    circ_path = base_dir / "target.circ"
    info_path = base_dir / "info.txt"

    if not circ_path.exists() or not info_path.exists():
        pytest.fail(f"测试素材缺失于 {base_dir}。请确保存在 target.circ 和 info.txt")

    config = ConfigManager.load_config(Path("config/config.toml"))

    # 2. 构造指令任务
    prompt_text = info_path.read_text(encoding="utf-8")
    task = TaskRecord(
        task_id="custom_case",
        task_name="expert_verification",
        task_type="verification",
        analysis_raw=prompt_text,
    )

    # 3. 初始化现代客户端并运行智能体
    client = create_genai_client(
        api_key=config.gemini.api_key,
        base_url=config.gemini.base_url,
    )

    agent = VerificationAgent(config, client)

    print(f"\n[Sandbox] Starting Expert Verification...")
    print(f"[Sandbox] Instruction: {prompt_text[:50]}...")

    try:
        updated_task = await agent.run(task, circ_path)

        # 4. 产出物归档
        if updated_task.status == "finished":
            # 导出文字报告
            (base_dir / "output.txt").write_text(
                updated_task.analysis_raw, encoding="utf-8"
            )

            # 导出截图
            if updated_task.assets:
                # 寻找最后一张 verified 截图
                src_img = Path(updated_task.assets[-1])
                # 如果是相对路径，尝试补全
                if not src_img.exists():
                    # 尝试从 output 目录寻找
                    src_img = Path("output") / updated_task.assets[-1]

                if src_img.exists():
                    shutil.copy(src_img, base_dir / "output.png")
                    print(
                        f"[Sandbox] SUCCESS: output.png and output.txt generated in {base_dir}"
                    )
                else:
                    print(
                        f"[Sandbox] WARNING: Could not find verified image at {src_img}"
                    )

            assert (base_dir / "output.txt").exists()
        else:
            pytest.fail(f"Agent 运行失败: {updated_task.analysis_raw}")

    finally:
        agent.close()


if __name__ == "__main__":
    asyncio.run(test_verification_case_sandbox())
