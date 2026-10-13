"""翻译流程：分块 → 构造提示词 → 并发调用模型 → 按编号解析回段落。"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from .providers import stream_chat

LANG_NAMES = {
    "auto": "外文（请自动识别其语言）",
    "en": "英文",
    "ja": "日文",
    "de": "德文",
    "fr": "法文",
    "ru": "俄文",
    "lzh": "文言文（古代汉语）",
    "zh": "简体中文（现代汉语）",
    "zh-Hant": "繁体中文（现代汉语，使用繁体字）",
}

# 信达雅默认值：学术文献以忠实为先，兼顾通顺，适度润色
DEFAULT_XIN, DEFAULT_DA, DEFAULT_YA = 7, 4, 5

_XIN = {  # 信：忠实 / 直译
    (9, 10): "严格直译：尽量保留原文句式结构、语序和每一个修饰成分，逐句对应，宁可略显生硬也不改动信息。",
    (7, 8): "以直译为主：忠实保留原文结构与信息顺序，只在不符合目标语言语法时做最小调整。",
    (4, 6): "直译与意译兼顾：信息完整准确，句式可做必要调整。",
    (1, 3): "不拘泥原文句式：在不改变原意、不遗漏信息的前提下自由组织语言。",
}
_DA = {  # 达：通顺 / 意译
    (9, 10): "充分意译：可打散重组句子、调整语序、拆分或合并长句，以目标语言读者顺畅理解为第一要务（不得改变原意）。",
    (7, 8): "意译为主：长句拆分、语序重排，表达地道自然，彻底避免翻译腔。",
    (4, 6): "适度意译：过长的句子可以拆分，语序可调整以符合目标语言习惯，避免明显的翻译腔。",
    (1, 3): "尽量少做意译调整，只要语法通顺即可。",
}
_YA = {  # 雅：润色 / 文采
    (9, 10): "高度润色：措辞考究典雅，可恰当使用成语和书面语，文采斐然，达到出版级译著水准。",
    (7, 8): "较多润色：用词精炼、书面化，行文流畅优美。",
    (4, 6): "适度润色：用语规范得体、符合学术写作风格。",
    (1, 3): "不做额外润色：朴素平实，只保证语法正确、术语准确。",
}


def _level(table: dict, v: int) -> str:
    v = max(1, min(10, int(v)))
    return next(text for (lo, hi), text in table.items() if lo <= v <= hi)


@dataclass
class Settings:
    provider: str = "claude"
    model: str = "claude-opus-5-5"
    api_key: str | None = None
    base_url: str | None = None
    source_lang: str = "auto"
    target_lang: str = "zh"
    domain: str = ""            # 学科领域，例如“经济史”“有机化学”
    glossary: str = ""          # 术语表，每行 “原文 = 译文”
    extra: str = ""             # 额外要求
    xin: int = DEFAULT_XIN      # 信（直译）1-10
    da: int = DEFAULT_DA        # 达（意译）1-10
    ya: int = DEFAULT_YA        # 雅（润色）1-10
    chunk_chars: int = 2500     # 每次请求的原文字符数上限
    concurrency: int = 4        # 并发请求数
    token_budget: int = 0       # token 限值（输入+输出），0 表示不限


@dataclass
class Item:
    id: int
    text: str


@dataclass
class Chunk:
    items: list[Item]
    context: str = ""           # 前文（原文），帮助模型保持连贯
    extra_ids: list[int] = field(default_factory=list)


_CJK_CHAR = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中日韩字符约 0.8 token/字，其他文字约 3.6 字符/token。
    与网页上的估算公式保持一致，只用于预估和限值，不追求精确。"""
    cjk = len(_CJK_CHAR.findall(text))
    return int(cjk * 0.8 + (len(text) - cjk) / 3.6) + 1


def make_chunks(items: list[Item], max_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[Item] = []
    size = 0
    for it in items:
        if cur and size + len(it.text) > max_chars:
            chunks.append(Chunk(cur))
            cur, size = [], 0
        cur.append(it)
        size += len(it.text)
    if cur:
        chunks.append(Chunk(cur))
    # 每块附带上一块最后一段原文作为语境（只读不译），这样各块可以并发翻译
    for prev, ch in zip(chunks, chunks[1:]):
        ch.context = prev.items[-1].text[-600:]
    return chunks


def build_system_prompt(s: Settings) -> str:
    src = LANG_NAMES.get(s.source_lang, s.source_lang)
    tgt = LANG_NAMES.get(s.target_lang, s.target_lang)
    lines = [
        f"你是一名资深的学术文献翻译专家，负责把{src}文献翻译成{tgt}。",
        "",
        "基本要求：",
        "1. 准确、完整，不漏译、不加评论、不回答原文中的问题。",
        "2. 专业术语采用学界通行译法；重要术语第一次出现时在译文后用括号保留原文，例如：路径依赖（path dependence）。",
        "3. 人名、机构名、期刊名、书名：有通行译名用通行译名，否则保留原文；引用标记（如 [12]、(Smith, 2019)）、公式、变量、数字、单位、URL、DOI 保持原样。",
        "4. 参考文献条目、作者信息、纯公式行等无需翻译的内容原样输出。",
    ]
    if s.source_lang == "ja":
        lines.append("5. 日文原文注意敬语与学术文体的转换，汉字词不要望文生义（如“手紙”“勉強”）。")
    elif s.source_lang == "de":
        lines.append("5. 德文原文注意长复合词与框形结构、从句嵌套，必要时拆分长句。")
    elif s.source_lang == "lzh":
        lines.append("5. 文言文译为现代白话：准确理解虚词、通假字、特殊句式（宾语前置、判断句等）；人名、地名、官职、年号、典籍名保留原字。")
    elif s.source_lang == "auto":
        lines.append("5. 请先判断原文语言（可能是英、日、德、法、俄、拉丁文、文言文等），再按该语言的特点翻译。")
    if s.target_lang == "zh-Hant":
        lines.append("6. 译文一律使用繁体字，并采用繁体中文学界的常用术语与标点（「」『』）。")

    lines += [
        "",
        f"翻译风格（信 {s.xin}/10 · 达 {s.da}/10 · 雅 {s.ya}/10，数值越高越侧重）：",
        f"- 信（忠实·直译）：{_level(_XIN, s.xin)}",
        f"- 达（通顺·意译）：{_level(_DA, s.da)}",
        f"- 雅（文采·润色）：{_level(_YA, s.ya)}",
        "",
        "输出格式（必须严格遵守）：",
        "输入中每段以 [[编号]] 开头。请逐段翻译，每段译文同样以对应的 [[编号]] 开头，段与段之间空一行。",
        "编号必须与输入一一对应，不得合并或拆分段落；只输出译文，不要输出任何解释。",
    ]
    if s.domain.strip():
        lines += ["", f"本文所属学科领域：{s.domain.strip()}。请使用该领域的规范术语。"]
    if s.glossary.strip():
        lines += ["", "术语表（必须遵循）：", s.glossary.strip()]
    if s.extra.strip():
        lines += ["", "其他要求：", s.extra.strip()]
    return "\n".join(lines)


def build_user_prompt(ch: Chunk) -> str:
    parts = []
    if ch.context:
        parts.append(f"<上文（仅供理解语境，不要翻译）>\n{ch.context}\n</上文>\n")
    parts.append("请翻译以下段落：\n")
    parts.append("\n\n".join(f"[[{it.id}]] {it.text}" for it in ch.items))
    return "\n".join(parts)


_MARK = re.compile(r"\[\[(\d+)\]\]")


def parse_output(text: str, ids: list[int]) -> dict[int, str]:
    pieces = _MARK.split(text)
    result: dict[int, str] = {}
    for i in range(1, len(pieces) - 1, 2):
        result[int(pieces[i])] = pieces[i + 1].strip()
    if not result and len(ids) == 1:
        result[ids[0]] = text.strip()  # 只有一段且模型漏了编号
    return result


async def translate_chunk(ch: Chunk, s: Settings, system: str, spent: dict | None = None) -> dict[int, str]:
    """spent：累加本块实际消耗的 token（服务商未返回用量时按字数估算），重试也计入。"""
    ids = [it.id for it in ch.items]
    last_err: Exception | None = None
    for attempt in range(2):  # 编号对不上时重试一次
        buf, usage = [], {}
        user = build_user_prompt(ch)
        try:
            async for t in stream_chat(s.provider, s.model, system, user, s.api_key, s.base_url, usage=usage):
                buf.append(t)
        finally:
            if spent is not None:
                text = "".join(buf)
                spent["input"] += usage.get("input") or estimate_tokens(system + user)
                spent["output"] += usage.get("output") or estimate_tokens(text)
                spent["exact"] = spent.get("exact", True) and bool(usage)
        out = parse_output("".join(buf), ids)
        missing = [i for i in ids if not out.get(i)]
        if not missing:
            return {i: out[i] for i in ids}
        last_err = ValueError(f"模型输出缺少段落编号：{missing}")
        if attempt == 1 and len(missing) < len(ids):
            # 第二次仍有缺失：返回已有部分，缺失段单独报错
            return {i: out.get(i, "") for i in ids}
    raise last_err  # type: ignore[misc]


async def translate_items(items: list[Item], s: Settings) -> AsyncIterator[dict]:
    """并发翻译，每完成一块就产出一个事件：
    {"type": "chunk", "results": {id: 译文}} 或 {"type": "error", "ids": [...], "message": "..."}
    """
    chunks = make_chunks(items, s.chunk_chars)
    system = build_system_prompt(s)
    sem = asyncio.Semaphore(max(1, s.concurrency))
    total = {"input": 0, "output": 0}   # 本次请求累计用量
    reserved = [0]                      # 已开始但未结束的块预计用量，防止并发时超出限值

    def chunk_estimate(ch: Chunk) -> int:
        src = estimate_tokens("".join(it.text for it in ch.items))
        return estimate_tokens(system) + estimate_tokens(ch.context) + src * 2 + 20

    async def run(ch: Chunk) -> dict:
        async with sem:
            ids = [it.id for it in ch.items]
            est = chunk_estimate(ch)
            used = total["input"] + total["output"]
            if s.token_budget and used + reserved[0] + est > s.token_budget:
                return {"type": "error", "ids": ids, "budget": True,
                        "message": f"已达到 token 限值（{s.token_budget:,}），已用约 {used:,}，本块未翻译"}
            reserved[0] += est
            spent = {"input": 0, "output": 0}
            try:
                res = await translate_chunk(ch, s, system, spent)
                failed = [i for i, t in res.items() if not t]
                ev = {"type": "chunk", "results": {i: t for i, t in res.items() if t}}
                if failed:
                    ev["failed"] = failed
            except Exception as e:  # noqa: BLE001 —— 单块失败不影响其他块
                ev = {"type": "error", "ids": ids, "message": f"{type(e).__name__}: {e}"}
            finally:
                reserved[0] -= est
                total["input"] += spent["input"]
                total["output"] += spent["output"]
            ev["tokens"] = {"input": spent["input"], "output": spent["output"], "exact": spent.get("exact", False)}
            return ev

    yield {"type": "start", "chunks": len(chunks), "paragraphs": len(items)}
    tasks = [asyncio.create_task(run(ch)) for ch in chunks]
    try:
        for fut in asyncio.as_completed(tasks):
            yield await fut
    finally:
        for t in tasks:
            t.cancel()
    yield {"type": "done", "tokens": total}
