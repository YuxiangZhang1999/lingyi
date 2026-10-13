"""灵译 · 网页服务入口。启动：python app.py  然后浏览器打开 http://127.0.0.1:8000"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, Response, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from translator.core import DEFAULT_DA, DEFAULT_XIN, DEFAULT_YA, Item, Settings, translate_items  # noqa: E402
from translator.export import FORMATS, export  # noqa: E402
from translator.extract import extract  # noqa: E402
from translator.glossary import generate_glossary  # noqa: E402
from translator.providers import FREE_MODEL, PROVIDERS, ProviderError, free_extra_body, has_server_key  # noqa: E402

BASE = Path(__file__).parent
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
# 免费术语表的防滥用限制：每个 IP 每小时次数、全站每天总次数、单次最多处理的字符数
FREE_PER_IP_HOUR = int(os.getenv("FREE_GLOSSARY_PER_IP_HOUR", "10"))
FREE_PER_DAY = int(os.getenv("FREE_GLOSSARY_PER_DAY", "500"))
FREE_MAX_CHARS = int(os.getenv("FREE_GLOSSARY_MAX_CHARS", "60000"))

app = FastAPI(title="灵译")


def check_password(x_app_password: str = Header(default="")):
    """部署到公网时设置 APP_PASSWORD，防止别人用你服务器上的 API Key。"""
    if APP_PASSWORD and x_app_password != APP_PASSWORD:
        raise HTTPException(401, "访问密码错误")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/config")
def config():
    return {
        "password_required": bool(APP_PASSWORD),
        "style_defaults": {"xin": DEFAULT_XIN, "da": DEFAULT_DA, "ya": DEFAULT_YA},
        "providers": {
            k: {"label": v["label"], "models": v["models"], "server_key": has_server_key(k),
                "base_url": v["base_url"] or ""}
            for k, v in PROVIDERS.items() if not v.get("hidden")
        },
        "free_glossary": {"enabled": has_server_key("free"), "label": FREE_MODEL["label"],
                          "per_hour": FREE_PER_IP_HOUR, "max_chars": FREE_MAX_CHARS},
    }


@app.post("/api/extract", dependencies=[Depends(check_password)])
async def api_extract(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_MB} MB")
    try:
        paras, pages = extract(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": file.filename, "paragraphs": paras, "pages": pages}


class TranslateReq(BaseModel):
    items: list[dict]  # [{"id": 0, "text": "..."}]
    provider: str = "claude"
    model: str = "claude-opus-5-5"
    api_key: str | None = None
    base_url: str | None = None
    source_lang: str = "auto"
    target_lang: str = "zh"
    domain: str = ""
    glossary: str = ""
    extra: str = ""
    xin: int = Field(DEFAULT_XIN, ge=1, le=10)
    da: int = Field(DEFAULT_DA, ge=1, le=10)
    ya: int = Field(DEFAULT_YA, ge=1, le=10)
    chunk_chars: int = Field(2500, ge=300, le=20000)
    concurrency: int = Field(4, ge=1, le=16)


@app.post("/api/translate", dependencies=[Depends(check_password)])
async def api_translate(req: TranslateReq):
    if req.provider not in PROVIDERS or PROVIDERS[req.provider].get("hidden"):
        raise HTTPException(400, "不支持的服务商")
    items = [Item(int(x["id"]), str(x["text"])) for x in req.items if str(x.get("text", "")).strip()]
    if not items:
        raise HTTPException(400, "没有需要翻译的内容")
    s = Settings(**req.model_dump(exclude={"items"}))

    async def gen():
        # NDJSON：每行一个 JSON 事件，前端边收边显示
        async for ev in translate_items(items, s):
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class GlossaryReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=2_000_000)
    use_free: bool = True              # True：内置免费模型；False：用户自己选的模型和 Key
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str | None = None
    base_url: str | None = None
    source_lang: str = "auto"
    target_lang: str = "zh"
    domain: str = ""


_ip_hits: dict[str, deque] = defaultdict(deque)
_day_hits: deque = deque()


def _client_ip(request: Request) -> str:
    # 部署在 Caddy 之后：Caddy 会写入真实客户端 IP（取最后一个，避免被伪造的请求头欺骗）
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[-1].strip() if xff else (request.client.host if request.client else "?")


def _take_free_quota(ip: str):
    now = time.time()
    hits = _ip_hits[ip]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    while _day_hits and now - _day_hits[0] > 86400:
        _day_hits.popleft()
    if len(hits) >= FREE_PER_IP_HOUR:
        wait = int(3600 - (now - hits[0])) // 60 + 1
        raise HTTPException(429, f"免费额度已用完：每小时最多 {FREE_PER_IP_HOUR} 次，请约 {wait} 分钟后再试，或改用自己的模型")
    if len(_day_hits) >= FREE_PER_DAY:
        raise HTTPException(429, "今日全站免费额度已用完，请明天再试，或改用自己的模型")
    hits.append(now)
    _day_hits.append(now)


@app.post("/api/glossary", dependencies=[Depends(check_password)])
async def api_glossary(req: GlossaryReq, request: Request):
    common = dict(source_lang=req.source_lang, target_lang=req.target_lang, domain=req.domain)
    try:
        if req.use_free:
            if not has_server_key("free"):
                raise HTTPException(503, "本站暂未配置免费模型，请改用自己的模型")
            _take_free_quota(_client_ip(request))
            result = await generate_glossary(
                req.text, provider="free", model=FREE_MODEL["model"], extra_body=free_extra_body(),
                max_chars=FREE_MAX_CHARS, **common)
            result["model"] = FREE_MODEL["label"]
        else:
            if req.provider not in PROVIDERS or PROVIDERS[req.provider].get("hidden"):
                raise HTTPException(400, "不支持的服务商")
            result = await generate_glossary(
                req.text, provider=req.provider, model=req.model, api_key=req.api_key,
                base_url=req.base_url, max_chars=200_000, **common)
            result["model"] = req.model
    except ProviderError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"模型调用失败：{type(e).__name__}: {e}")
    return result


class ExportReq(BaseModel):
    title: str = "译文"
    paras: list[dict]                 # [{"src": 原文, "tgt": 译文, "page": 页索引}]
    pages: list[dict] | None = None   # [{"header": 页眉, "footer": 页脚}]
    format: str = Field("docx", pattern="^(docx|pdf|md|txt)$")
    bilingual: bool = True
    preserve_layout: bool = False     # 保留页眉、页脚、页码与分页


@app.post("/api/export", dependencies=[Depends(check_password)])
def api_export(req: ExportReq):
    data = export(req.format, req.title, req.paras, req.pages, req.bilingual, req.preserve_layout)
    mime, ext = FORMATS[req.format]
    fname = quote(f"{req.title}_{'双语' if req.bilingual else '译文'}.{ext}")
    return Response(data, media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
