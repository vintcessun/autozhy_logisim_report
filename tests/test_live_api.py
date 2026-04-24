import sys
from pathlib import Path

# Fix sys.path for direct execution
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
from src.utils.config_loader import ConfigManager
from src.utils.llm_client import create_genai_client
import pytest


@pytest.mark.asyncio
async def test_live_api():
    print("--- 正在加载真实配置 ---")
    config = ConfigManager.load_config("config/config.toml")

    # 1. 尝试使用配置的所有模型名
    models_to_try = [config.gemini.model_flash, config.gemini.model_pro]

    print(f"\n--- [Probing] API Key: {config.gemini.api_key[:10]}*** ---")

    api_key = config.gemini.api_key
    base_url = config.gemini.base_url

    success_count = 0
    for model_name in models_to_try:
        print(f"\n>> 尝试模型: {model_name}")

        try:
            client = create_genai_client(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "Respond with 'PONG'"}],
            )
            text = response.choices[0].message.content or ""

            print(f"SUCCESS with {model_name}! Response: {text.strip()}")
            success_count += 1

        except Exception as e:
            print(f"Probe failed for {model_name}: {e}")

    if success_count == 0:
        print("\n[CRITICAL] 所有探测均告失败。请检查：")
        print("1. 该中转是否支持原生 Google SDK 调用？")
        print("2. 模型名称是否在你的中转后台已启用？")
    else:
        print(f"\n--- 探测结束: {success_count}/{len(models_to_try)} 模型成功 ---")


if __name__ == "__main__":
    asyncio.run(test_live_api())
