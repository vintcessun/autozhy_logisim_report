import os
import requests
from typing import Dict, Any

def search_web(query: str) -> str:
    """
    通过 SearXNG 搜素引擎查询互联网知识。
    参数:
        query: 搜素关键词。
    """
    base_url = os.getenv("SEARXNG_URL", "http://localhost:8089/")
    try:
        url = f"{base_url}search?q={query}&format=json"
        response = requests.get(url, timeout=10)
        data = response.json()
        results = data.get("results", [])[:5]
        if not results:
            return "未找到相关结果。"
        return "\n".join([f"- {r.get('title')}: {r.get('content')}" for r in results])
    except Exception as e:
        return f"搜索执行失败: {str(e)}"

def python_interpreter(code: str) -> str:
    """
    执行 Python 代码沙盒。
    用于验证数学模型、电路拓扑推导或调用自检工具。
    参数:
        code: 要运行的 Python 代码。
    """
    import sys
    from io import StringIO
    from src.utils.internal_verifier import self_verify_cla
    
    old_stdout = sys.stdout
    redirected_output = StringIO()
    sys.stdout = redirected_output
    
    # 定义安全环境，注入常用库和自检工具
    import math, json, re, itertools
    safe_globals = {
        "math": math,
        "json": json,
        "re": re,
        "itertools": itertools,
        "print": print,
        "self_verify_cla": self_verify_cla,
        "range": range,
        "len": len,
        "list": list,
        "dict": dict
    }
    
    try:
        exec(code, safe_globals)
        return redirected_output.getvalue()
    except Exception as e:
        return f"执行异常: {str(e)}\n已输出内容: {redirected_output.getvalue()}"
    finally:
        sys.stdout = old_stdout

# 导出的工具函数列表，供 SDK 使用
tools_list = [search_web, python_interpreter]
