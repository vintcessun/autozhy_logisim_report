import asyncio
import random
from typing import Any, Callable, Coroutine
from google.genai import errors

# 全局 429 计数器，用于触发长延迟
_consecutive_429s = 0

async def retry_llm_call(func: Callable[..., Any], *args, max_retries: int = 3, initial_delay: float = 2.0, **kwargs) -> Any:
    """
    一个通用的异步大模型调用重试包装器。
    引入 10 分钟“冷却期”机制，应对上游饱和。
    """
    global _consecutive_429s
    retries = 0
    while True:
        try:
            # 现代 SDK 通常是同步阻断式调用
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            # 成功后重置 429 计数器
            _consecutive_429s = 0
            return result
            
        except Exception as e:
            retries += 1
            is_retryable = False
            error_msg = str(e)
            
            # 对 429 进行特殊记录
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                is_retryable = True
                _consecutive_429s += 1
            elif any(code in error_msg for code in ["500", "502", "503", "504", "Bad Gateway", "Gateway Timeout"]):
                is_retryable = True
            
            # 触发长等待机制
            if _consecutive_429s >= 3:
                print(f"\n[AI CRITICAL] 连续检测到 3 次 429 报错，上游已饱和。进入 15 秒冷静期...")
                _consecutive_429s = 0 # 重置计数器以便冷却后重启
                await asyncio.sleep(15)
                continue # 冷静后重新开始本轮尝试
            
            if not is_retryable or retries > max_retries:
                print(f"[AI Retry] 达到最大重试次数或遇到不可重试错误: {e}")
                raise e
            
            # 常规指数退避
            delay = initial_delay * (2 ** (retries - 1)) + random.uniform(0, 1)
            print(f"[AI Retry] 遇到错误 ({error_msg})，正在进行第 {retries}/{max_retries} 次重试，等待 {delay:.2f}s...")
            await asyncio.sleep(delay)
