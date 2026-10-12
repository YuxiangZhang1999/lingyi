"""灵译 · 命令行版本，适合批量翻译。

示例：
  python translate_cli.py paper.pdf -p claude -m claude-opus-5-5 --preserve-layout -f pdf
  python translate_cli.py paper.pdf -p deepseek -m deepseek-chat --domain 经济史 -f md
  python translate_cli.py 論文.pdf -p gemini -m gemini-2.5-pro --src ja --glossary terms.txt
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from translator.core import DEFAULT_DA, DEFAULT_XIN, DEFAULT_YA, Item, Settings, translate_items
from translator.export import FORMATS, export
from translator.extract import extract
from translator.providers import PROVIDERS


async def main():
    ap = argparse.ArgumentParser(description="用大模型翻译外文文献")
    ap.add_argument("file", help="PDF / DOCX / TXT / MD 文件")
    ap.add_argument("-p", "--provider", default="claude", choices=list(PROVIDERS))
    ap.add_argument("-m", "--model", default=None, help="模型名，默认取该服务商的第一个建议模型")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--src", default="auto", help="原文语言：auto/en/ja/de/fr/ru/lzh(文言文)")
    ap.add_argument("--tgt", default="zh", help="zh / zh-Hant(繁体) / en")
    ap.add_argument("--xin", type=int, default=DEFAULT_XIN, help="信（直译）1-10")
    ap.add_argument("--da", type=int, default=DEFAULT_DA, help="达（意译）1-10")
    ap.add_argument("--ya", type=int, default=DEFAULT_YA, help="雅（润色）1-10")
    ap.add_argument("--domain", default="", help="学科领域")
    ap.add_argument("--glossary", default=None, help="术语表文件，每行“原文 = 译文”")
    ap.add_argument("--chunk-chars", type=int, default=2500)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("-f", "--format", default="docx", choices=list(FORMATS))
    ap.add_argument("--preserve-layout", action="store_true", help="保留页眉、页脚、页码与分页（仅 PDF 输入）")
    ap.add_argument("--only-translation", action="store_true", help="只输出译文（默认双语对照）")
    ap.add_argument("-o", "--output", default=None)
    a = ap.parse_args()

    path = Path(a.file)
    paras, pages = extract(path.name, path.read_bytes())
    print(f"提取到 {len(paras)} 段")

    s = Settings(
        provider=a.provider,
        model=a.model or (PROVIDERS[a.provider]["models"] or [""])[0],
        base_url=a.base_url, source_lang=a.src, target_lang=a.tgt, domain=a.domain,
        glossary=Path(a.glossary).read_text("utf-8") if a.glossary else "",
        xin=a.xin, da=a.da, ya=a.ya, chunk_chars=a.chunk_chars, concurrency=a.concurrency,
    )
    result: dict[int, str] = {}
    async for ev in translate_items([Item(i, p["text"]) for i, p in enumerate(paras)], s):
        if ev["type"] == "start":
            total, done = ev["chunks"], 0
        elif ev["type"] == "chunk":
            done += 1
            result.update(ev["results"])
            print(f"  [{done}/{total}] 完成")
        elif ev["type"] == "error":
            done += 1
            print(f"  [{done}/{total}] 第 {ev['ids'][0] + 1}-{ev['ids'][-1] + 1} 段失败：{ev['message']}")

    rows = [{"src": p["text"], "tgt": result.get(i, ""), "page": p["page"]} for i, p in enumerate(paras)]
    bilingual = not a.only_translation
    data = export(a.format, path.stem, rows, pages, bilingual, a.preserve_layout)
    out = Path(a.output or path.with_name(f"{path.stem}_{'双语' if bilingual else '译文'}.{a.format}"))
    out.write_bytes(data)
    print(f"已保存：{out}（{sum(1 for r in rows if r['tgt'])}/{len(rows)} 段已翻译）")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
