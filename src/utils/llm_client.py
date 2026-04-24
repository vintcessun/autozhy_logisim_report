from openai import OpenAI


def normalize_openai_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/v1beta"):
        endpoint = endpoint[:-7]
    if not endpoint.endswith("/v1"):
        endpoint = f"{endpoint}/v1"
    return endpoint


def create_openai_client(
    api_key: str,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> OpenAI:
    endpoint = normalize_openai_base_url(base_url)
    # timeout=None 表示无限等待；配置为 <=0 时也按无限等待处理。
    timeout: float | None = (
        None if (timeout_seconds is None or timeout_seconds <= 0) else timeout_seconds
    )
    if endpoint:
        return OpenAI(api_key=api_key, base_url=endpoint, timeout=timeout)
    return OpenAI(api_key=api_key, timeout=timeout)


def normalize_genai_base_url(base_url: str | None) -> str | None:
    """将 OpenAI 兼容形式的 base_url 规整为 Gemini 原生 endpoint。

    许多代理（jisuai 等）同时暴露 `/v1/` (OpenAI 兼容) 和根路径下的
    `/v1beta/models/...` (Gemini 原生)。google-genai SDK 会自动拼接
    `/v1beta/...`，因此这里只需要保留到站点根即可。
    """
    if not base_url:
        return None
    endpoint = base_url.rstrip("/")
    for suffix in ("/v1beta", "/v1"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
            break
    return endpoint or None


def create_genai_client(api_key: str, base_url: str | None = None):
    """创建一个 google-genai 原生客户端。

    用于 ReAct + Function Calling 场景：原生 API 在回塞 tool_response 时
    会自动保留 `thought_signature`，不会出现 OpenAI 兼容层丢签名的 400。
    """
    from google import genai
    from google.genai import types as genai_types

    http_options = None
    endpoint = normalize_genai_base_url(base_url)
    if endpoint:
        http_options = genai_types.HttpOptions(base_url=endpoint)
    return genai.Client(api_key=api_key, http_options=http_options)
