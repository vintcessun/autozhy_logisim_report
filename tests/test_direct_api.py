import asyncio
from pathlib import Path
import google.generativeai as genai
from src.utils.config_loader import ConfigManager

async def test_direct_api():
    print("--- 正在加载真实配置 (直连模式) ---")
    config = ConfigManager.load_config("config/config.toml")
    
    # 彻底尊重用户在 .toml 中的每一个字符
    api_endpoint = config.gemini.base_url
    print(f"[API] 秘钥: {config.gemini.api_key[:10]}***")
    print(f"[API] 地址: {api_endpoint}")
    
    genai.configure(
        api_key=config.gemini.api_key,
        transport="rest",
        client_options={"api_endpoint": api_endpoint}
    )
    
    print(f"\n>> 正在对模型 {config.gemini.model_flash} 发起真机挑战...")
    try:
        model = genai.GenerativeModel(config.gemini.model_flash)
        response = await model.generate_content_async("Respond with 'DIRECT_CONNECTION_SUCCESS'")
        print(f"Response: {response.text.strip()}")
    except Exception as e:
        print(f"Direct Connection Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_direct_api())
