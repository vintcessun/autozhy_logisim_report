import asyncio
from openai import AsyncOpenAI
from src.utils.config_loader import ConfigManager
from pathlib import Path

async def test_openai_protocol():
    print("--- 正在加载配置 (OpenAI 协议探测) ---")
    config = ConfigManager.load_config("config/config.toml")
    
    # 适配中转地址：OpenAI 库通常需要 /v1 结尾
    base_url = config.gemini.base_url
    if "v1beta" in base_url:
        base_url = base_url.replace("v1beta", "v1")
    
    print(f"[OpenAI] Key: {config.gemini.api_key[:10]}***")
    print(f"[OpenAI] Base URL: {base_url}")
    print(f"[OpenAI] Model: {config.gemini.model_flash}")

    client = AsyncOpenAI(
        api_key=config.gemini.api_key,
        base_url=base_url
    )

    print(f"\n--- [Probing] API Key: {config.gemini.api_key[:10]}*** ---")

    # 1. 尝试拉取模型列表
    print("\n>> 正在尝试获取中转商支持的模型清单...")
    try:
        models = await client.models.list()
        print("成功获取模型列表:")
        for m in models.data:
            print(f" - {m.id}")
        return # 成功拉取则退出
    except Exception as e:
        print(f"获取模型列表失败: {e}")

    # 2. 尝试最通用的模型名 (GPT-4o-mini) 验证账号状态
    try:
        print("\n>> 尝试通用模型 gpt-4o-mini 验证账号连通性...")
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "PING"}]
        )
        print(f"SUCCESS with gpt-4o-mini! Response: {response.choices[0].message.content.strip()}")
    except Exception as e:
        print(f"gpt-4o-mini failed: {e}")

    print("\n--- [Test B] 视觉多模态测试 ---")
    image_path = Path("debug/handshake_v0.png")
    if image_path.exists():
        import base64
        def encode_image(path):
            with open(path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        
        base64_image = encode_image(image_path)
        try:
            response = await client.chat.completions.create(
                model=config.gemini.model_pro,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What menus are visible in this screenshot?"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                        ]
                    }
                ]
            )
            print(f"Vision Response: {response.choices[0].message.content.strip()}")
        except Exception as e:
            print(f"Vision Test Failed: {e}")
    else:
        print("Skipping Vision Test: No screenshot found.")

if __name__ == "__main__":
    asyncio.run(test_openai_protocol())
