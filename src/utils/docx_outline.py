"""解析实验指导书 docx，提取 3.x 分点式大纲。

设计原则：docx 的样式/排版全然不可靠（List Paragraph vs Normal、缩进级别、换行
被人为断行等），靠规则硬拆只会越写越脆。因此本模块只负责把 docx 段落拉成**纯文本**，
大纲结构（章节 → 实验组 → 问题列表）完全交由 LLM 读取正文后产出。

产物结构：
    sections = [
        {
            "num": "3.1",
            "title": "验证实验",
            "groups": [
                {
                    "index": 1,
                    "description": "ROM存储器组件电路、RAM存储器组件电路...",
                    "questions": ["问题1...", "问题2..."],
                },
                ...
            ],
        },
        ...
    ]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, cast

from docx import Document


_SECTION_TYPE = {
    "3.1": "verification",
    "3.2": "design",
    "3.3": "challenge",
}


def _iter_docx_candidates(instruction_docs: List[str]) -> List[Path]:
    paths: List[Path] = []
    for raw in instruction_docs or []:
        p = Path(raw)
        if not p.exists():
            continue
        if p.suffix.lower() != ".docx":
            continue
        if p.name.startswith("~$"):
            continue
        paths.append(p)
    return paths


def extract_docx_text(instruction_docs: List[str]) -> str:
    """把 instruction_docs 中第一个可用 docx 的段落拼成纯文本。

    不做任何结构判断，只按顺序把非空段落用换行连接。未找到 docx 返回空串。
    """
    for path in _iter_docx_candidates(instruction_docs):
        doc = Document(str(path))
        lines = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        return "\n".join(lines)
    return ""


_OUTLINE_PROMPT = """你是实验指导书大纲结构化助手。下面是一份实验指导书 docx 抽取出的纯文本正文。
请从中解析出第 3 章的三个小节——3.1 验证实验、3.2 设计实验、3.3 挑战性实验——的结构化大纲。

【输出要求】严格输出 JSON，格式如下：
{
  "sections": [
    {
      "num": "3.1",
      "title": "验证实验",
      "groups": [
        {"index": 1, "description": "该实验组的完整原文描述（可多行，原样保留）", "questions": ["问题1", "问题2"]}
      ]
    },
    {"num": "3.2", "title": "设计实验", "groups": [...]},
    {"num": "3.3", "title": "挑战性实验", "groups": [...]}
  ]
}

【解析规则】
1. 只输出 3.1 / 3.2 / 3.3 三节；其他前言、实验环境、实验目的、封面等一律忽略。
2. 每节的 groups 应与指导书原文的"分组/分条"一一对应——不要合并、不要拆碎、不要漏号。
   - 3.1：每个实验组通常对应一段主题描述 + 紧随其后的"回答问题"列表。
   - 3.2：每个设计实验通常是一条独立描述（可能包含"——设计文件名：xxx.circ"）。
   - 3.3：若原文写明"请从以下N组中任选1组"，则按"第1组/第2组/..."为边界切分；引导语
     （含设计文件名）要**拼进每个 group 的 description 头部**，确保每条 group 独立可读。
3. `description` 必须保留原文语义和关键信息（电路名、块数、文件名、加分数等），可多行。
   不要总结、不要改写、不要翻译。多行之间用 \\n 分隔。
4. `questions` 仅填"回答问题"或类似小节下的问题条目；没有就给空数组 []。
5. `index` 从 1 开始，按原文顺序编号。
6. 忽略"实验报告提交/命名/上传"等尾部元信息。

【docx 正文开始】
__DOCX_TEXT__
【docx 正文结束】
"""


async def llm_parse_outline(
    instruction_docs: List[str],
    client: Any,
    model_id: str,
) -> List[Dict[str, Any]]:
    """用 LLM 解析 docx 大纲。无 docx 时返回 []；解析失败直接抛异常。"""
    docx_text = extract_docx_text(instruction_docs)
    if not docx_text:
        return []

    # 延迟导入，避免顶层循环依赖
    from .ai_utils import generate_content_with_tools
    from google.genai import types as genai_types

    prompt = _OUTLINE_PROMPT.replace("__DOCX_TEXT__", docx_text)

    history: list[Any] = [
        genai_types.Content(
            role="user", parts=[genai_types.Part.from_text(text=prompt)]
        )
    ]
    last_err: Optional[str] = None
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        response = await generate_content_with_tools(
            client,
            model=model_id,
            contents=history,
            config={"response_mime_type": "application/json"},
        )
        raw = (response.text or "").strip()
        history.append(
            genai_types.Content(
                role="model", parts=[genai_types.Part.from_text(text=raw)]
            )
        )
        try:
            data = cast(Any, json.loads(raw))
            sections_raw = data.get("sections") if isinstance(data, dict) else None
            if not isinstance(sections_raw, list) or not sections_raw:
                raise ValueError("缺少 sections 数组或为空")
            sections = cast(List[Any], sections_raw)
            normalized: List[Dict[str, Any]] = []
            for sec in sections:
                sec_d = cast(Dict[str, Any], sec) if isinstance(sec, dict) else None
                if sec_d is None:
                    continue
                num = str(sec_d.get("num", "")).strip()
                title = str(sec_d.get("title", "")).strip()
                groups_in = sec_d.get("groups", [])
                if not num or not isinstance(groups_in, list):
                    continue
                groups_list = cast(List[Any], groups_in)
                groups_out: List[Dict[str, Any]] = []
                for i, g in enumerate(groups_list, start=1):
                    g_d = cast(Dict[str, Any], g) if isinstance(g, dict) else None
                    if g_d is None:
                        continue
                    desc = str(g_d.get("description", "")).strip()
                    if not desc:
                        continue
                    qs_in = g_d.get("questions", []) or []
                    if not isinstance(qs_in, list):
                        qs_in = []
                    questions = [
                        str(q).strip() for q in cast(List[Any], qs_in) if str(q).strip()
                    ]
                    try:
                        idx_val = int(g_d.get("index", i)) or i
                    except (TypeError, ValueError):
                        idx_val = i
                    groups_out.append(
                        {
                            "index": idx_val,
                            "description": desc,
                            "questions": questions,
                        }
                    )
                if groups_out:
                    normalized.append(
                        {"num": num, "title": title, "groups": groups_out}
                    )
            if not normalized:
                raise ValueError("规范化后大纲为空")
            return normalized
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < max_retries:
                history.append(
                    genai_types.Content(
                        role="user",
                        parts=[
                            genai_types.Part.from_text(
                                text=(
                                    f"上条回复 JSON 解析失败：{last_err}。"
                                    "请严格按原要求的 schema 重新输出 JSON，不要任何多余文本。"
                                )
                            )
                        ],
                    )
                )

    raise RuntimeError(f"LLM 解析 docx 大纲连续失败 {max_retries} 次：{last_err}")


def format_outline_for_prompt(sections: List[Dict[str, Any]]) -> str:
    """把大纲格式化成可读字符串（供下游 LLM prompt 使用）。

    章节号到 task_type 的映射是硬规则（3.1→verification / 3.2→design / 3.3→challenge）。
    """
    lines: List[str] = []
    for sec in sections:
        tt = _SECTION_TYPE.get(str(sec["num"]), "verification")
        lines.append(f"{sec['num']} {sec['title']}  [task_type={tt}]")
        for g in sec["groups"]:
            gid = f"{sec['num']}-{g['index']}"
            lines.append(f"  [{gid}] {g['description']}")
            for q in g["questions"]:
                lines.append(f"    - 问: {q}")
    return "\n".join(lines)
