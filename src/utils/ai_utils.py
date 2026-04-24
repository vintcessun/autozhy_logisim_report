import asyncio
import base64
import inspect
import io
import json
import random
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

import PIL.Image

# 全局 429 计数器，用于触发长延迟
_consecutive_429s = 0
_DEFAULT_AFC_REMOTE_CALLS = 64
_DEFAULT_AFC_CONTINUATION_ROUNDS = 4


@dataclass
class LLMResponse:
    text: str
    automatic_function_calling_history: list[Any]


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


def _annotation_to_json_type(annotation: Any) -> str:
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    return "string"


def _callable_to_openai_tool(tool: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(tool)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        properties[param_name] = {
            "type": _annotation_to_json_type(param.annotation),
        }
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": tool.__name__,
            "description": (tool.__doc__ or "").strip() or f"Call {tool.__name__}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _normalize_tools_for_openai(tools: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if callable(tool):
            normalized.append(_callable_to_openai_tool(tool))
        elif isinstance(tool, dict):
            normalized.append(tool)
    return normalized


def build_tool_enabled_config(
    base_config: dict[str, Any] | None = None,
    *,
    maximum_remote_calls: int = _DEFAULT_AFC_REMOTE_CALLS,
    extra_tools: list[Any] | None = None,
) -> dict[str, Any]:
    from .tool_definitions import tools_list

    if isinstance(base_config, dict):
        config_data = dict(base_config)
    elif base_config is None:
        config_data = {}
    else:
        raise TypeError(f"Unsupported config type: {type(base_config)}")

    existing_tools = config_data.get("tools") or []
    if not isinstance(existing_tools, list):
        existing_tools = list(existing_tools)
    merged_base = list(tools_list) + (list(extra_tools) if extra_tools else [])
    config_data["tools"] = _merge_tools(existing_tools, merged_base)

    afc_data = config_data.get("automatic_function_calling") or {}
    afc_payload = dict(afc_data)

    current_limit = afc_payload.get("maximum_remote_calls")
    afc_payload["disable"] = False
    afc_payload["maximum_remote_calls"] = max(
        int(current_limit) if current_limit is not None else 0,
        maximum_remote_calls,
    )
    config_data["automatic_function_calling"] = afc_payload
    config_data["tools"] = _normalize_tools_for_openai(config_data["tools"])

    return config_data


def _extract_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    return text.strip() if isinstance(text, str) else ""


def _extract_afc_history(response: Any) -> list[Any]:
    history = getattr(response, "automatic_function_calling_history", None)
    return history if isinstance(history, list) else []


def _extract_openai_message(completion: Any) -> Any | None:
    try:
        choices = getattr(completion, "choices", None)
        if choices and len(choices) > 0:
            return choices[0].message
    except Exception:
        return None
    return None


def _pil_image_to_data_url(image: PIL.Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _contents_to_message(contents: Any) -> dict[str, Any]:
    if isinstance(contents, str):
        return {"role": "user", "content": contents}
    if not isinstance(contents, list):
        return {"role": "user", "content": str(contents)}

    content_parts: list[dict[str, Any]] = []
    text_segments: list[str] = []
    for item in contents:
        if isinstance(item, PIL.Image.Image):
            if text_segments:
                content_parts.append(
                    {"type": "text", "text": "\n\n".join(text_segments)}
                )
                text_segments = []
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _pil_image_to_data_url(item)},
                }
            )
        elif isinstance(item, str):
            text_segments.append(item)
        else:
            text_segments.append(str(item))

    if text_segments:
        content_parts.append({"type": "text", "text": "\n\n".join(text_segments)})

    if len(content_parts) == 1 and content_parts[0].get("type") == "text":
        return {"role": "user", "content": content_parts[0]["text"]}
    return {"role": "user", "content": content_parts}


def _messages_from_contents(contents: Any) -> list[dict[str, Any]]:
    if isinstance(contents, list) and contents and isinstance(contents[0], dict):
        return list(contents)
    return [_contents_to_message(contents)]


def _build_tool_map(tools: list[Any]) -> dict[str, Callable[..., Any]]:
    return {tool.__name__: tool for tool in tools if callable(tool)}


async def _run_openai_chat_completion(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    response_mime_type: str | None,
) -> Any:
    def _extract_delta_text(content_delta: Any) -> str:
        if isinstance(content_delta, str):
            return content_delta
        if isinstance(content_delta, list):
            text_parts: list[str] = []
            for part in content_delta:
                if isinstance(part, str):
                    text_parts.append(part)
                    continue
                text_value = getattr(part, "text", None)
                if isinstance(text_value, str):
                    text_parts.append(text_value)
            return "".join(text_parts)
        return ""

    def _call() -> Any:
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if response_mime_type == "application/json":
            request["response_format"] = {"type": "json_object"}

        # Gemini 2.5 (via OpenAI-compatible proxy) 在 functionCall 上会挂
        # thought_signature，必须原样回传；而 OpenAI 兼容层的 streaming
        # delta 根本不暴露该字段，导致第 2 轮注入 tool 结果时 400。
        # 解决：对带工具的调用禁用 thinking（budget=0），从源头不产生签名。
        # reasoning_effort 是 OpenAI 官方字段，Gemini 代理也兼容。
        if tools:
            request.setdefault("reasoning_effort", "none")
            extra_body = dict(request.get("extra_body") or {})
            google_cfg = dict(extra_body.get("google") or {})
            thinking_cfg = dict(google_cfg.get("thinking_config") or {})
            thinking_cfg.setdefault("thinking_budget", 0)
            thinking_cfg.setdefault("include_thoughts", False)
            google_cfg["thinking_config"] = thinking_cfg
            extra_body["google"] = google_cfg
            # 某些代理读取顶层 thinking_config
            extra_body.setdefault("thinking_config", thinking_cfg)
            request["extra_body"] = extra_body

        request_started_at = time.monotonic()
        first_chunk_at: list[float | None] = [None]
        stream_started_at: list[float | None] = [None]

        # 共享状态（由主循环写，后台线程只读）
        received_bytes: list[int] = [0]
        progress_done = threading.Event()

        # 单一后台线程：三个阶段全部在同一行原地刷新
        def _progress() -> None:
            while not progress_done.wait(0.2):
                now = time.monotonic()
                fca = first_chunk_at[0]
                if fca is None:
                    # 阶段一：等待首包
                    elapsed = now - request_started_at
                    print(f"\r[AI 等待首包] {elapsed:.1f}s …", end="", flush=True)
                else:
                    # 阶段二：流式接收中
                    ssa = stream_started_at[0] or fca
                    elapsed = now - ssa
                    nb = received_bytes[0]
                    wait_s = fca - request_started_at
                    print(
                        f"\r[AI 首包 {wait_s:.1f}s | 接收中] 已收 {nb:,} B | 传输 {elapsed:.1f}s …",
                        end="",
                        flush=True,
                    )

        progress_thread = threading.Thread(target=_progress, daemon=True)
        progress_thread.start()

        text_segments: list[str] = []
        reasoning_segments: list[str] = []
        aggregated_tool_calls: dict[int, dict[str, Any]] = {}
        usage: Any = None
        failure: BaseException | None = None

        try:
            stream = client.chat.completions.create(**request)
            for chunk in stream:
                if first_chunk_at[0] is None:
                    first_chunk_at[0] = time.monotonic()
                    stream_started_at[0] = first_chunk_at[0]

                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = chunk_usage

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                text_delta = _extract_delta_text(getattr(delta, "content", None))
                if text_delta:
                    nb = len(text_delta.encode("utf-8"))
                    received_bytes[0] += nb
                    text_segments.append(text_delta)

                # Gemini(OpenAI 兼容)思考签名：流式 delta 会以 reasoning_content /
                # reasoning 字段分段返回。必须原样保留并在下一轮作为 assistant
                # 消息的 reasoning_content 回塞，否则上游会 400 “Function call
                # is missing a thought_signature”。
                for attr in ("reasoning_content", "reasoning"):
                    raw_reasoning = getattr(delta, attr, None)
                    if isinstance(raw_reasoning, str) and raw_reasoning:
                        reasoning_segments.append(raw_reasoning)

                delta_tool_calls = getattr(delta, "tool_calls", None) or []
                for call in delta_tool_calls:
                    idx = int(getattr(call, "index", 0) or 0)
                    entry = aggregated_tool_calls.setdefault(
                        idx,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                            "thought_signature": "",
                        },
                    )
                    call_id = getattr(call, "id", None)
                    if isinstance(call_id, str) and call_id:
                        entry["id"] = call_id
                    call_type = getattr(call, "type", None)
                    if isinstance(call_type, str) and call_type:
                        entry["type"] = call_type
                    # 逐 tool_call 的思考签名（不同代理字段名不一致，全都兜住）
                    for sig_attr in (
                        "thought_signature",
                        "reasoning_signature",
                        "signature",
                    ):
                        sig_val = getattr(call, sig_attr, None)
                        if isinstance(sig_val, str) and sig_val:
                            entry["thought_signature"] += sig_val
                    fn = getattr(call, "function", None)
                    if fn is None:
                        continue
                    fn_name = getattr(fn, "name", None)
                    if isinstance(fn_name, str) and fn_name:
                        if entry["function"]["name"]:
                            entry["function"]["name"] += fn_name
                        else:
                            entry["function"]["name"] = fn_name
                    fn_args = getattr(fn, "arguments", None)
                    if isinstance(fn_args, str) and fn_args:
                        entry["function"]["arguments"] += fn_args
        except BaseException as exc:
            failure = exc
            raise
        finally:
            progress_done.set()
            progress_thread.join(timeout=0.5)
            if failure is not None:
                failure_text = str(failure).strip() or type(failure).__name__
                print(f"\r[AI 失败] {failure_text}" + " " * 20)

        output_text = "".join(text_segments)
        total_bytes = received_bytes[0]

        if first_chunk_at[0] is None:
            total_wait = time.monotonic() - request_started_at
            print(f"\r[AI 完成] 未收到首包，总等待 {total_wait:.2f}s" + " " * 20)
            stream_elapsed = 0.0
            wait_elapsed = total_wait
        else:
            fca = first_chunk_at[0]
            ssa = stream_started_at[0] or fca
            stream_elapsed = time.monotonic() - ssa
            wait_elapsed = fca - request_started_at

        completion_tokens = getattr(usage, "completion_tokens", None)
        if completion_tokens is None and total_bytes > 0:
            completion_tokens = max(1, len(output_text) // 4)
        token_display = completion_tokens if completion_tokens is not None else "未知"

        # 结束：同一行覆盖进度，末尾换行
        print(
            f"\r[AI 完成] 等待首包 {wait_elapsed:.2f}s | 传输 {stream_elapsed:.2f}s | "
            f"接收 {total_bytes:,} B | token {token_display}" + " " * 5
        )

        tool_calls_list: list[Any] = []
        for idx in sorted(aggregated_tool_calls):
            call = aggregated_tool_calls[idx]
            call_id = call["id"] or f"tool_call_{idx}"
            fn_payload = call["function"]
            tool_calls_list.append(
                SimpleNamespace(
                    id=call_id,
                    type=call["type"],
                    function=SimpleNamespace(
                        name=fn_payload["name"],
                        arguments=fn_payload["arguments"],
                    ),
                    thought_signature=call.get("thought_signature", ""),
                )
            )

        reasoning_text = "".join(reasoning_segments)
        message = SimpleNamespace(
            content=output_text,
            tool_calls=tool_calls_list,
            reasoning_content=reasoning_text,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
        )

    return await asyncio.to_thread(_call)


async def retry_llm_call(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 8,
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
            else:
                # 网络层错误（连接断开 / 超时 / DNS / SSL 等）同样可重试
                # 依靠异常类型名 + 消息关键词，避免过度匹配
                exc_type_name = type(e).__name__
                network_exc_types = {
                    "RemoteProtocolError",
                    "ConnectError",
                    "ConnectTimeout",
                    "ReadTimeout",
                    "ReadError",
                    "WriteError",
                    "PoolTimeout",
                    "ProxyError",
                    "NetworkError",
                    "ConnectionError",
                    "ConnectionResetError",
                    "ConnectionAbortedError",
                    "TimeoutError",
                    "IncompleteRead",
                }
                network_msg_hints = (
                    "Server disconnected",
                    "Connection reset",
                    "Connection aborted",
                    "Connection refused",
                    "ECONNRESET",
                    "EOF occurred",
                    "SSLError",
                    "timed out",
                    "Temporary failure in name resolution",
                )
                if exc_type_name in network_exc_types or any(
                    h in error_msg for h in network_msg_hints
                ):
                    is_retryable = True

            # 触发长等待机制
            if _consecutive_429s >= 10:
                print(
                    f"\n[AI CRITICAL] 连续检测到 10 次 429 报错，上游已饱和。进入 15 秒冷静期..."
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
    config: dict[str, Any] | None = None,
    max_continuation_rounds: int = _DEFAULT_AFC_CONTINUATION_ROUNDS,
    extra_tools: list[Any] | None = None,
) -> LLMResponse:
    """
    统一 LLM 调用入口：使用 google-genai 原生 API 执行（带工具或纯文本）。

    参数保持与旧 OpenAI 兼容实现一致以避免改动调用方：
    - client: google.genai.Client 实例
    - config: 可带 response_mime_type / automatic_function_calling.maximum_remote_calls
    - extra_tools: 仅本次调用生效的附加可调用工具
    """
    from .tool_definitions import tools_list

    config_data = dict(config) if isinstance(config, dict) else {}
    response_mime_type = config_data.get("response_mime_type")

    afc = config_data.get("automatic_function_calling") or {}
    afc_limit = afc.get("maximum_remote_calls") if isinstance(afc, dict) else None
    max_rounds = max(
        1,
        int(afc_limit) if afc_limit is not None else _DEFAULT_AFC_REMOTE_CALLS,
    )
    max_rounds = min(max_rounds, max_continuation_rounds * _DEFAULT_AFC_REMOTE_CALLS)

    callable_tools: list[Callable[..., Any]] = [
        t for t in list(tools_list) if callable(t)
    ]
    if extra_tools:
        for t in extra_tools:
            if callable(t):
                callable_tools.append(t)

    # 没有工具且只需要一次性文本/JSON 响应：走简化路径，日志更安静
    if not callable_tools:
        return await generate_react_native(
            client,
            model=model,
            contents=contents,
            tools=[],
            max_rounds=1,
            response_mime_type=response_mime_type,
            log_prefix="AI",
            verbose_thought=False,
        )

    return await generate_react_native(
        client,
        model=model,
        contents=contents,
        tools=callable_tools,
        max_rounds=max_rounds,
        response_mime_type=response_mime_type,
        log_prefix="AI AFC",
        verbose_thought=False,
    )


# ============================================================================
# 原生 Gemini API (google-genai) 的 ReAct 工具循环
#
# 目的：绕开 OpenAI 兼容代理在转发 tool_call 时丢失 `thought_signature`
# 导致的 400 报错。原生 SDK 会把模型返回的 `Content` 对象原样保留，
# 其中的签名字段在下一轮 `contents` 里自动随 parts 带回上游。
# ============================================================================


def _contents_to_genai_parts(contents: Any) -> list[Any]:
    """将 str / list[str|PIL.Image] / Part 列表归一化为 google-genai parts。"""
    from google.genai import types as genai_types

    items = contents if isinstance(contents, list) else [contents]
    parts: list[Any] = []
    for item in items:
        if isinstance(item, genai_types.Part):
            parts.append(item)
        elif isinstance(item, str):
            parts.append(genai_types.Part.from_text(text=item))
        elif isinstance(item, PIL.Image.Image):
            buf = io.BytesIO()
            fmt = (item.format or "PNG").upper()
            mime = "image/png" if fmt == "PNG" else f"image/{fmt.lower()}"
            item.save(buf, format=fmt)
            parts.append(
                genai_types.Part.from_bytes(data=buf.getvalue(), mime_type=mime)
            )
        else:
            parts.append(genai_types.Part.from_text(text=str(item)))
    return parts


def _callable_to_genai_declaration(tool: Callable[..., Any]) -> Any:
    from google.genai import types as genai_types

    sig = inspect.signature(tool)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        properties[name] = {"type": _annotation_to_json_type(param.annotation)}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return genai_types.FunctionDeclaration(
        name=tool.__name__,
        description=(tool.__doc__ or "").strip() or f"Call {tool.__name__}",
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


async def generate_react_native(
    genai_client: Any,
    *,
    model: str,
    contents: Any,
    tools: list[Callable[..., Any]],
    max_rounds: int = 48,
    is_done: Callable[[], bool] | None = None,
    response_mime_type: str | None = None,
    log_prefix: str = "AI ReAct",
    verbose_thought: bool = True,
) -> LLMResponse:
    """使用 google-genai 原生 API 运行 ReAct 工具循环。

    - tools 中的 Python 可调用会被转换为 FunctionDeclaration；
    - 我们手动执行函数（支持 async），并把 FunctionResponse 回塞；
    - 关键点：把模型返回的 `candidate.content` 原样追加进 `contents`，
      从而天然保留每个 functionCall 上的 `thought_signature`。
    - `is_done` 回调若返回 True 则立即终止循环（例如 report_verdict 命中）。
    - `response_mime_type="application/json"` 时要求模型直接输出 JSON。
    """
    from google.genai import types as genai_types

    tool_map: dict[str, Callable[..., Any]] = {
        t.__name__: t for t in tools if callable(t)
    }
    declarations = [_callable_to_genai_declaration(t) for t in tools if callable(t)]
    tool_param = (
        [genai_types.Tool(function_declarations=declarations)] if declarations else None
    )

    # contents 全程保持为 list[Content|Part]
    initial_parts = _contents_to_genai_parts(contents)
    working_contents: list[Any] = [
        genai_types.Content(role="user", parts=initial_parts)
    ]

    config_kwargs: dict[str, Any] = {
        "automatic_function_calling": genai_types.AutomaticFunctionCallingConfig(
            disable=True  # 手动执行循环，保留签名传递与 async 支持
        ),
        # 请求模型返回思维摘要，便于日志里观察推理轨迹（part.thought=True）。
        "thinking_config": genai_types.ThinkingConfig(include_thoughts=True),
    }
    if tool_param is not None:
        config_kwargs["tools"] = tool_param
    if response_mime_type:
        config_kwargs["response_mime_type"] = response_mime_type
    config = genai_types.GenerateContentConfig(**config_kwargs)

    final_text = ""
    for round_index in range(max_rounds):
        round_label = f"[{log_prefix} R{round_index + 1:03d}]"

        def _sync_call() -> Any:
            return genai_client.models.generate_content(
                model=model,
                contents=working_contents,
                config=config,
            )

        t_start = time.monotonic()
        response = await retry_llm_call(asyncio.to_thread, _sync_call)
        elapsed_model = time.monotonic() - t_start

        candidate = None
        if getattr(response, "candidates", None):
            candidate = response.candidates[0]
        content = getattr(candidate, "content", None) if candidate else None
        parts = getattr(content, "parts", None) or []

        function_calls: list[Any] = []
        text_segments: list[str] = []
        thought_segments: list[str] = []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc is not None:
                function_calls.append(fc)
                continue
            text_val = getattr(part, "text", None)
            if isinstance(text_val, str) and text_val:
                # google-genai 用 part.thought=True 标记思维摘要文本
                if getattr(part, "thought", False):
                    thought_segments.append(text_val)
                else:
                    text_segments.append(text_val)

        final_text = "".join(text_segments).strip() or final_text

        # token 使用量（若 SDK 返回）
        usage = getattr(response, "usage_metadata", None)
        token_info = ""
        if usage is not None:
            prompt_tok = getattr(usage, "prompt_token_count", None)
            cand_tok = getattr(usage, "candidates_token_count", None)
            total_tok = getattr(usage, "total_token_count", None)
            thought_tok = getattr(usage, "thoughts_token_count", None)
            bits = []
            if prompt_tok is not None:
                bits.append(f"in={prompt_tok}")
            if cand_tok is not None:
                bits.append(f"out={cand_tok}")
            if thought_tok is not None:
                bits.append(f"thought={thought_tok}")
            if total_tok is not None:
                bits.append(f"total={total_tok}")
            if bits:
                token_info = " | " + " ".join(bits)

        finish_reason = ""
        if candidate is not None:
            fr = getattr(candidate, "finish_reason", None)
            if fr is not None:
                finish_reason = f" | finish={getattr(fr, 'name', str(fr))}"

        if verbose_thought:
            print(
                f"{round_label} model={elapsed_model:.2f}s"
                f" calls={len(function_calls)} text_parts={len(text_segments)}"
                f" thoughts={len(thought_segments)}{token_info}{finish_reason}"
            )
        else:
            print(
                f"{round_label} model={elapsed_model:.2f}s calls={len(function_calls)}{token_info}"
            )

        # 打印思维摘要和普通文本的前若干字符，便于定位模型意图
        if verbose_thought and thought_segments:
            preview = " / ".join(s.strip().replace("\n", " ") for s in thought_segments)
            if len(preview) > 300:
                preview = preview[:300] + "…"
            print(f"{round_label} thought: {preview}")
        if verbose_thought and text_segments:
            preview = " / ".join(s.strip().replace("\n", " ") for s in text_segments)
            if len(preview) > 300:
                preview = preview[:300] + "…"
            print(f"{round_label} text: {preview}")

        # 关键：保留模型 Content（含签名）回塞下一轮
        if content is not None:
            working_contents.append(content)

        if not function_calls:
            # 正常结束（STOP）或缺失 finish_reason 时才真正退出。
            # MALFORMED_FUNCTION_CALL / MAX_TOKENS 等异常 finish 但又没产生
            # function_call 时，不能直接终止——把当前文字打到控制台，
            # 注入一条 user 消息要求模型继续，进入下一轮。
            fr_name = ""
            if candidate is not None:
                fr = getattr(candidate, "finish_reason", None)
                if fr is not None:
                    fr_name = getattr(fr, "name", str(fr)) or ""

            normal_stop = fr_name in ("", "STOP", "FINISH_REASON_UNSPECIFIED")
            if normal_stop:
                if verbose_thought:
                    print(f"{round_label} 无工具调用，终止循环。")
                return LLMResponse(
                    text=final_text,
                    automatic_function_calling_history=working_contents,
                )

            # 异常 finish：打印模型已经吐出来的文字（若有），然后续跑
            if text_segments:
                full_text = "".join(text_segments).strip()
                if full_text:
                    print(f"{round_label} text(异常 finish={fr_name}): {full_text}")
            else:
                print(
                    f"{round_label} 异常 finish={fr_name} 且无文本输出，"
                    f"注入续跑提示。"
                )

            nudge = (
                f"上一轮以 finish_reason={fr_name} 结束，且未产生有效的工具调用。"
                "请继续完成任务：要么发起一个格式正确的工具调用，"
                "要么直接给出最终答案文本。不要重复上一轮被截断或非法的内容。"
            )
            working_contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=nudge)],
                )
            )
            continue

        # 执行所有工具调用并构造 FunctionResponse parts
        response_parts: list[Any] = []
        # 工具如果返回 {"__image__": bytes, "mime_type": "image/png", ...}，
        # 会把 bytes 单独装到一个 user-role Content 里追加在函数响应之后，
        # 让模型真正"看到"截图。
        pending_image_parts: list[Any] = []
        for idx, fc in enumerate(function_calls, start=1):
            fname = getattr(fc, "name", "") or ""
            fargs = getattr(fc, "args", None) or {}
            if isinstance(fargs, str):
                try:
                    fargs = json.loads(fargs)
                except Exception:
                    fargs = {}
            if not isinstance(fargs, dict):
                fargs = {}

            args_preview = json.dumps(fargs, ensure_ascii=False, default=str)
            if len(args_preview) > 200:
                args_preview = args_preview[:200] + "…"

            tool_fn = tool_map.get(fname)
            t_tool = time.monotonic()
            if tool_fn is None:
                observation = {"error": f"工具不存在: {fname}"}
                tool_elapsed = 0.0
            else:
                try:
                    if inspect.iscoroutinefunction(tool_fn):
                        raw = await tool_fn(**fargs)
                    else:
                        raw = tool_fn(**fargs)
                except Exception as exc:
                    raw = f"工具异常: {exc}"
                tool_elapsed = time.monotonic() - t_tool
                observation = _coerce_function_response(raw)

            # 检测图像返回约定
            image_bytes: bytes | None = None
            image_mime = "image/png"
            if isinstance(observation, dict) and "__image__" in observation:
                candidate_img = observation.pop("__image__")
                image_mime = str(observation.pop("__image_mime__", "image/png"))
                if isinstance(candidate_img, (bytes, bytearray)) and candidate_img:
                    image_bytes = bytes(candidate_img)

            obs_preview = json.dumps(observation, ensure_ascii=False, default=str)
            if len(obs_preview) > 300:
                obs_preview = obs_preview[:300] + "…"
            img_note = f" +image({len(image_bytes)}B)" if image_bytes else ""
            print(
                f"{round_label} tool[{idx}/{len(function_calls)}] "
                f"{fname}({args_preview}) => {obs_preview}{img_note}  ({tool_elapsed:.2f}s)"
            )

            response_parts.append(
                genai_types.Part.from_function_response(
                    name=fname, response=observation
                )
            )

            if image_bytes is not None:
                pending_image_parts.append(
                    genai_types.Part.from_bytes(data=image_bytes, mime_type=image_mime)
                )
                pending_image_parts.append(
                    genai_types.Part.from_text(
                        text=f"[上图为 {fname} 返回的截图，请结合其中信息继续推理]"
                    )
                )

        working_contents.append(genai_types.Content(role="user", parts=response_parts))

        if pending_image_parts:
            working_contents.append(
                genai_types.Content(role="user", parts=pending_image_parts)
            )

        if is_done is not None and is_done():
            # 模型已通过 report_verdict 上报判定，无需再请求一次补全。
            print(f"{round_label} 判定已上报，终止循环。")
            return LLMResponse(
                text=final_text,
                automatic_function_calling_history=working_contents,
            )

    return LLMResponse(
        text=final_text,
        automatic_function_calling_history=working_contents,
    )


def _coerce_function_response(raw: Any) -> dict[str, Any]:
    """google-genai 要求 FunctionResponse.response 是 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                decoded = json.loads(s)
                if isinstance(decoded, dict):
                    return decoded
                return {"result": decoded}
            except Exception:
                pass
        return {"result": s}
    return {"result": raw}
