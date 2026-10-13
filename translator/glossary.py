"""自动生成术语表：分块抽取术语 → 合并去重 → 按出现频次排序。"""
from __future__ import annotations

import asyncio
import re

from .core import LANG_NAMES
from .providers import stream_chat

CHUNK_CHARS = 6000   # 每次请求处理的原文长度
MAX_TERMS = 200      # 最多返回的术语条数

_LINE = re.compile(r"^\s*(?:[-*•\d.、)]+\s*)?(.+?)\s*[=＝]\s*(.+?)\s*$")


def build_prompt(source_lang: str, target_lang: str, domain: str) -> str:
    src = LANG_NAMES.get(source_lang, source_lang)
    tgt = LANG_NAMES.get(target_lang, target_lang)
    lines = [
        f"你是学术翻译的术语专家。请从用户给出的{src}文本中抽取需要统一译法的术语，并给出规范的{tgt}译名。",
        "",
        "抽取范围：",
        "1. 学科专业术语、核心概念、理论与方法名称；",
        "2. 人名、地名、机构名、文献/书刊名、历史事件、法规制度名等专有名词；",
        "3. 在文中反复出现、译法需要前后一致的关键词组。",
        "不要抽取：常见普通词汇、整句、数字、公式、引用标记。",
        "",
        "译名要求：采用学界通行译法；人名有通行译名用通行译名，否则音译；没有把握时保留原文。",
        "",
        "输出格式（严格遵守）：每行一条，格式为“原文 = 译名”，不要编号、不要解释、不要输出其他任何内容。",
    ]
    if domain.strip():
        lines.insert(1, f"文本所属学科领域：{domain.strip()}。")
    return "\n".join(lines)


def parse_terms(text: str) -> list[tuple[str, str]]:
    terms = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        src, tgt = m.group(1).strip(" \"'“”「」`*"), m.group(2).strip(" \"'“”「」`*")
        if 1 <= len(src) <= 80 and 1 <= len(tgt) <= 80 and src != "原文":
            terms.append((src, tgt))
    return terms


def split_chunks(text: str, max_total: int) -> tuple[list[str], bool]:
    """切成若干块；超过 max_total 时在全文中均匀抽取若干块，保证覆盖全篇。"""
    text = re.sub(r"[ \t]+", " ", text).strip()
    chunks, buf = [], ""
    for para in re.split(r"\n\s*\n|\n", text):
        if buf and len(buf) + len(para) > CHUNK_CHARS:
            chunks.append(buf)
            buf = ""
        buf += para + "\n"
        while len(buf) > CHUNK_CHARS:  # 超长段落硬切
            chunks.append(buf[:CHUNK_CHARS])
            buf = buf[CHUNK_CHARS:]
    if buf.strip():
        chunks.append(buf)
    keep = max(1, max_total // CHUNK_CHARS)
    if len(chunks) <= keep:
        return chunks, False
    step = len(chunks) / keep
    return [chunks[int(i * step)] for i in range(keep)], True


async def generate_glossary(
    text: str, *, provider: str, model: str, api_key: str | None = None, base_url: str | None = None,
    extra_body: dict | None = None, source_lang: str = "auto", target_lang: str = "zh", domain: str = "",
    max_chars: int = 60000, concurrency: int = 3,
) -> dict:
    chunks, truncated = split_chunks(text, max_chars)
    system = build_prompt(source_lang, target_lang, domain)
    sem = asyncio.Semaphore(concurrency)

    async def run(chunk: str) -> list[tuple[str, str]]:
        async with sem:
            buf = []
            async for t in stream_chat(provider, model, system, chunk, api_key, base_url, extra_body=extra_body):
                buf.append(t)
            return parse_terms("".join(buf))

    results = await asyncio.gather(*(run(c) for c in chunks), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors and len(errors) == len(results):
        raise errors[0]

    merged: dict[str, dict] = {}
    for res in results:
        if isinstance(res, Exception):
            continue
        for src, tgt in res:
            key = src.casefold()
            if key in merged:
                merged[key]["count"] += 1
            else:
                merged[key] = {"src": src, "tgt": tgt, "count": 1}
    # 原文中出现次数越多越靠前
    low = text.casefold()
    for t in merged.values():
        t["count"] += low.count(t["src"].casefold())
    terms = sorted(merged.values(), key=lambda t: -t["count"])[:MAX_TERMS]
    return {"terms": terms, "truncated": truncated, "chunks": len(chunks), "failed_chunks": len(errors)}
