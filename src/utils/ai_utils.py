import asyncio
import inspect
import random
from typing import Any, Callable

from google.genai import types

# 全局 429 计数器，用于触发长延迟
_consecutive_429s = 0
_DEFAULT_AFC_REMOTE_CALLS = 64
_DEFAULT_AFC_CONTINUATION_ROUNDS = 4


def _tool_identity(tool: Any) -> str:
    name = getattr(tool, "__name__", None)
    if name:
        return f"callable:{name}"
    return repr(tool)


def _merge_tools(existing_tools: list[Any], extra_tools: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for tool in [*existing_tools, *extra_tools]:
        key = _tool_identity(tool)
        if key in seen:
            continue
        seen.add(key)
        merged.append(tool)
    return merged


def build_tool_enabled_config(
    base_config: types.GenerateContentConfigOrDict | None = None,
    *,
    maximum_remote_calls: int = _DEFAULT_AFC_REMOTE_CALLS,
) -> types.GenerateContentConfig:
    from .tool_definitions import tools_list

    if isinstance(base_config, types.GenerateContentConfig):
        config_data: dict[str, Any] = base_config.model_dump(exclude_none=True)
    elif isinstance(base_config, dict):
        config_data = dict(base_config)
    elif base_config is None:
        config_data = {}
    else:
        raise TypeError(f"Unsupported config type: {type(base_config)}")

    existing_tools = config_data.get("tools") or []
    if not isinstance(existing_tools, list):
        existing_tools = list(existing_tools)
    config_data["tools"] = _merge_tools(existing_tools, list(tools_list))

    afc_data = config_data.get("automatic_function_calling") or {}
    if isinstance(afc_data, types.AutomaticFunctionCallingConfig):
        afc_payload = afc_data.model_dump(exclude_none=True)
    else:
        afc_payload = dict(afc_data)

    current_limit = afc_payload.get("maximum_remote_calls")
    afc_payload["disable"] = False
    afc_payload["maximum_remote_calls"] = max(
        int(current_limit) if current_limit is not None else 0,
        maximum_remote_calls,
    )
    config_data["automatic_function_calling"] = afc_payload

    return types.GenerateContentConfig(**config_data)


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    return text.strip() if isinstance(text, str) else ""


def _extract_afc_history(response: Any) -> list[Any]:
    history = getattr(response, "automatic_function_calling_history", None)
    return history if isinstance(history, list) else []


async def retry_llm_call(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """
    一个通用的异步大模型调用重试包装器。
    引入 10 分钟“冷却期”机制，应对上游饱和。
    """
    global _consecutive_429s
    retries = 0
    while True:
        try:
            # 现代 SDK 通常是同步阻断式调用
            if inspect.iscoroutinefunction(func):
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
            elif any(
                code in error_msg
                for code in [
                    "500",
                    "502",
                    "503",
                    "504",
                    "Bad Gateway",
                    "Gateway Timeout",
                ]
            ):
                is_retryable = True

            # 触发长等待机制
            if _consecutive_429s >= 3:
                print(
                    f"\n[AI CRITICAL] 连续检测到 3 次 429 报错，上游已饱和。进入 15 秒冷静期..."
                )
                _consecutive_429s = 0  # 重置计数器以便冷却后重启
                await asyncio.sleep(15)
                continue  # 冷静后重新开始本轮尝试

            if not is_retryable or retries > max_retries:
                print(f"[AI Retry] 达到最大重试次数或遇到不可重试错误: {e}")
                raise e

            # 常规指数退避
            delay = initial_delay * (2 ** (retries - 1)) + random.uniform(0, 1)
            print(
                f"[AI Retry] 遇到错误 ({error_msg})，正在进行第 {retries}/{max_retries} 次重试，等待 {delay:.2f}s..."
            )
            await asyncio.sleep(delay)


async def generate_content_with_tools(
    client: Any,
    *,
    model: str,
    contents: Any,
    config: types.GenerateContentConfigOrDict | None = None,
    max_continuation_rounds: int = _DEFAULT_AFC_CONTINUATION_ROUNDS,
) -> Any:
    """
    为所有 LLM 调用统一注入工具，并在 AFC 耗尽但仍未拿到正文时继续沿调用历史追问。
    """
    merged_config = build_tool_enabled_config(config)
    current_contents = contents
    last_history_size = -1
    response: Any = None

    for round_index in range(max_continuation_rounds + 1):
        response = await retry_llm_call(
            client.models.generate_content,
            model=model,
            contents=current_contents,
            config=merged_config,
        )

        if _extract_response_text(response):
            return response

        afc_history = _extract_afc_history(response)
        if not afc_history:
            return response

        if len(afc_history) <= last_history_size:
            return response

        last_history_size = len(afc_history)
        current_contents = afc_history
        print(
            f"[AI AFC] 模型返回了工具调用历史但尚无正文，继续第 {round_index + 1} 轮补全..."
        )

    return response
