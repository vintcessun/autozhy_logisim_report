import asyncio
from openai import AsyncOpenAI
from src.utils.config_loader import ConfigManager

async def test_openai_bridge():
    print("--- 正在加载配置 (OpenAI 桥接测试) ---")
    config = ConfigManager.load_config("config/config.toml")
    
    # OpenAI 协议的标准 Base URL 结尾通常是 /v1
    base_url = "https://api.jisuai.top/v1"
    
    print(f"[Bridge] 目标模型: {config.gemini.model_flash}")
    print(f"[Bridge] Base URL: {base_url}")

    client = AsyncOpenAI(
        api_key=config.gemini.api_key,
        base_url=base_url
    )

    try:
        print("\n>> 正在发起请求...")
        response = await client.chat.completions.create(
            model=config.gemini.model_flash,
            messages=[{"role": "user", "content": "PING"}]
        )
        print(f"SUCCESS! Gemini 回复: {response.choices[0].message.content.strip()}")
    except Exception as e:
        print(f"Bridge Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_openai_bridge())
