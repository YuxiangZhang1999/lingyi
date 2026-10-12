"""从 PDF / Word / 文本文件中提取段落。

返回值统一为 (paragraphs, pages)：
- paragraphs: [{"text": 段落文本, "page": 所在页索引或 None}]
- pages: PDF 才有，[{"header": 页眉, "footer": 页脚（含页码）}]；其他格式为 None
"""
from __future__ import annotations

import io
import re
from collections import Counter

_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_PAGE_NUM = re.compile(
    r"[\d\s\-–—/|.·]+|[ivxlcdmIVXLCDM]{1,6}|(?i:page|p\.|seite|s\.)\s*\d+(\s*(?i:of|/|von)\s*\d+)?|[-–—]?\s*\d+\s*[-–—]?|第?\s*\d+\s*[页頁]"
)
MARGIN = 0.12  # 页面上下各 12% 视为页眉/页脚区域


def _join_lines(lines: list[str]) -> str:
    """把 PDF 里被硬换行切开的行拼回一段：处理连字符断词，中日文/文言文不加空格。"""
    out = ""
    for line in (l.strip() for l in lines):
        if not line:
            continue
        if not out:
            out = line
        elif re.search(r"[A-Za-zÄÖÜäöüß]-$", out) and line[:1].islower():
            out = out[:-1] + line  # 英文/德文行尾连字符断词
        elif _CJK.search(out[-1:]) and _CJK.search(line[:1]):
            out += line
        else:
            out += " " + line
    return out


def split_text(text: str) -> list[str]:
    """纯文本按空行分段；单个换行视为段内换行。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = re.split(r"\n\s*\n", text)
    return [p for p in (_join_lines(p.split("\n")) for p in paras) if p]


def extract_pdf(data: bytes):
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    raw = []  # 每页：(页高, [(文本, y0, y1), ...])
    for page in doc:
        blocks = []
        for b in page.get_text("blocks", sort=True):
            if b[6] != 0:  # 跳过图片块
                continue
            t = _join_lines(b[4].split("\n"))
            if t:
                blocks.append((t, b[1], b[3]))
        raw.append((page.rect.height, blocks))
    doc.close()

    def norm(s: str) -> str:  # 页码数字不同的页眉页脚视为同一个
        return re.sub(r"\d+", "#", s)

    def in_margin(h, y0, y1):
        return y1 < h * MARGIN or y0 > h * (1 - MARGIN)

    n = len(raw)
    counter = Counter(
        norm(t) for h, bl in raw for t in {t for t, y0, y1 in bl if len(t) < 150 and in_margin(h, y0, y1)}
    )
    paras: list[dict] = []
    pages: list[dict] = []
    for pi, (h, bl) in enumerate(raw):
        head, foot = [], []
        for t, y0, y1 in bl:
            if in_margin(h, y0, y1) and len(t) < 150:
                repeated = n >= 3 and counter[norm(t)] >= max(3, n // 2)
                if repeated or _PAGE_NUM.fullmatch(t.strip()):
                    (head if (y0 + y1) / 2 < h / 2 else foot).append(t)
                    continue
            paras.append({"text": t, "page": pi})
        pages.append({"header": "  ".join(head), "footer": "  ".join(foot)})
    if not paras:
        raise ValueError("PDF 中没有可提取的文字，可能是扫描版（图片）。建议先用 OCR 工具转成 Markdown 再上传。")
    return _merge_broken(paras), pages


def _merge_broken(paras: list[dict]) -> list[dict]:
    """跨页/跨栏被切断的段落：上一段没有以句末标点结束、下一段以小写开头时合并（保留起始页）。"""
    merged: list[dict] = []
    for p in paras:
        if merged and not re.search(r"[.!?。！？:：”\")]$", merged[-1]["text"]) and p["text"][:1].islower():
            merged[-1]["text"] = _join_lines([merged[-1]["text"], p["text"]])
        else:
            merged.append(dict(p))
    return merged


def extract_docx(data: bytes) -> list[str]:
    import docx

    d = docx.Document(io.BytesIO(data))
    return [p.text.strip() for p in d.paragraphs if p.text.strip()]


def extract(filename: str, data: bytes):
    name = filename.lower()
    if name.endswith(".pdf"):
        return extract_pdf(data)
    if name.endswith(".docx"):
        texts = extract_docx(data)
    elif name.endswith((".txt", ".md", ".markdown", ".tex")):
        texts = None
        encs = ["utf-8-sig", "shift_jis", "gb18030", "latin-1"]
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            encs.insert(0, "utf-16")
        for enc in encs:
            try:
                texts = split_text(data.decode(enc))
                break
            except UnicodeDecodeError:
                continue
    else:
        raise ValueError("暂不支持该文件类型，请上传 PDF / DOCX / TXT / MD")
    return [{"text": t, "page": None} for t in texts or []], None
