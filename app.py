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
from translator.providers import (  # noqa: E402
    DEFAULT_PROVIDER, FREE_MODEL, PROVIDERS, ProviderError, has_server_key, uses_builtin_key)

BASE = Path(__file__).parent
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# ---- 内置免费额度（服务器上的智谱 Key）的防滥用限制，均可在 .env 中调整 ----
FREE_TRANSLATE_IP_DAY = int(os.getenv("FREE_TRANSLATE_CHARS_PER_IP_DAY", "100000"))  # 每个 IP 每天可免费翻译的原文字符数
FREE_TRANSLATE_DAY = int(os.getenv("FREE_TRANSLATE_CHARS_PER_DAY", "3000000"))       # 全站每天总字符数
FREE_CONCURRENCY = int(os.getenv("FREE_CONCURRENCY", "2"))                           # 免费模型的并发上限
FREE_GLOSSARY_IP_HOUR = int(os.getenv("FREE_GLOSSARY_PER_IP_HOUR", "10"))
FREE_GLOSSARY_DAY = int(os.getenv("FREE_GLOSSARY_PER_DAY", "500"))
FREE_GLOSSARY_MAX_CHARS = int(os.getenv("FREE_GLOSSARY_MAX_CHARS", "60000"))

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
    providers = {}
    for k, v in PROVIDERS.items():
        builtin = "builtin_models" in v and has_server_key(k)
        providers[k] = {
            "label": v["label"], "models": v["models"], "base_url": v["base_url"] or "",
            "key_url": v.get("key_url", ""),
            # builtin：本站提供免费额度，用户不填 Key 也能用（仅限 builtin_models 中的模型）
            "builtin": builtin, "builtin_models": v.get("builtin_models", []),
            "server_key": has_server_key(k) and not builtin,
        }
    return {
        "password_required": bool(APP_PASSWORD),
        "default_provider": DEFAULT_PROVIDER,
        "style_defaults": {"xin": DEFAULT_XIN, "da": DEFAULT_DA, "ya": DEFAULT_YA},
        "providers": providers,
        "free_quota": {"translate_chars_per_day": FREE_TRANSLATE_IP_DAY, "glossary_per_hour": FREE_GLOSSARY_IP_HOUR},
        "free_glossary": {"enabled": has_server_key(FREE_MODEL["provider"]), "label": FREE_MODEL["label"],
                          "per_hour": FREE_GLOSSARY_IP_HOUR, "max_chars": FREE_GLOSSARY_MAX_CHARS},
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


# ---------- 免费额度计数（内存中，服务重启后清零） ----------
class Quota:
    """滑动窗口限额：window 秒内累计用量不超过 limit。"""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)

    def used(self, key: str) -> int:
        q, now = self.hits[key], time.time()
        while q and now - q[0][0] > self.window:
            q.popleft()
        return sum(n for _, n in q)

    def take(self, key: str, amount: int, message: str):
        if self.used(key) + amount > self.limit:
            raise HTTPException(429, message)
        self.hits[key].append((time.time(), amount))


translate_ip_quota = Quota(FREE_TRANSLATE_IP_DAY, 86400)
translate_day_quota = Quota(FREE_TRANSLATE_DAY, 86400)
glossary_ip_quota = Quota(FREE_GLOSSARY_IP_HOUR, 3600)
glossary_day_quota = Quota(FREE_GLOSSARY_DAY, 86400)


def _client_ip(request: Request) -> str:
    # 部署在 Caddy 之后：Caddy 会写入真实客户端 IP（取最后一个，避免被伪造的请求头欺骗）
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[-1].strip() if xff else (request.client.host if request.client else "?")


def _check_provider(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(400, "不支持的服务商")


def _check_builtin(provider: str, model: str):
    allowed = PROVIDERS[provider]["builtin_models"]
    if not has_server_key(provider):
        raise HTTPException(400, f"本站暂未开通「{PROVIDERS[provider]['label']}」的免费额度，请填写你自己的 API Key")
    if model not in allowed:
        raise HTTPException(400, f"免费额度只能使用 {'、'.join(allowed)}，其他模型请填写你自己的 API Key")


class TranslateReq(BaseModel):
    items: list[dict]  # [{"id": 0, "text": "..."}]
    provider: str = DEFAULT_PROVIDER
    model: str = FREE_MODEL["model"]
    api_key: str | None = None
    base_url: str | None = None
    source_lang: str = "en"
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
async def api_translate(req: TranslateReq, request: Request):
    _check_provider(req.provider)
    items = [Item(int(x["id"]), str(x["text"])) for x in req.items if str(x.get("text", "")).strip()]
    if not items:
        raise HTTPException(400, "没有需要翻译的内容")
    s = Settings(**req.model_dump(exclude={"items"}))
    if uses_builtin_key(req.provider, req.api_key):
        _check_builtin(req.provider, req.model)
        chars, ip = sum(len(it.text) for it in items), _client_ip(request)
        left = max(0, FREE_TRANSLATE_IP_DAY - translate_ip_quota.used(ip))
        translate_ip_quota.take(ip, chars, f"今日免费翻译额度不足：每天 {FREE_TRANSLATE_IP_DAY // 10000} 万字符，"
                                           f"还剩约 {left} 字符，本次需要 {chars} 字符。可分批翻译，或填写自己的 API Key")
        translate_day_quota.take("all", chars, "今日全站免费额度已用完，请明天再试，或填写自己的 API Key")
        s.concurrency = min(s.concurrency, FREE_CONCURRENCY)  # 免费模型并发受限

    async def gen():
        # NDJSON：每行一个 JSON 事件，前端边收边显示
        async for ev in translate_items(items, s):
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class GlossaryReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=2_000_000)
    use_free: bool = True              # True：内置免费模型；False：用户自己选的模型和 Key
    provider: str = DEFAULT_PROVIDER
    model: str = FREE_MODEL["model"]
    api_key: str | None = None
    base_url: str | None = None
    source_lang: str = "en"
    target_lang: str = "zh"
    domain: str = ""


@app.post("/api/glossary", dependencies=[Depends(check_password)])
async def api_glossary(req: GlossaryReq, request: Request):
    common = dict(source_lang=req.source_lang, target_lang=req.target_lang, domain=req.domain)
    if req.use_free:
        provider, model, api_key, base_url = FREE_MODEL["provider"], FREE_MODEL["model"], None, None
    else:
        _check_provider(req.provider)
        provider, model, api_key, base_url = req.provider, req.model, req.api_key, req.base_url
    builtin = uses_builtin_key(provider, api_key)
    try:
        if builtin:
            _check_builtin(provider, model)
            ip = _client_ip(request)
            glossary_ip_quota.take(ip, 1, f"免费额度已用完：每小时最多生成 {FREE_GLOSSARY_IP_HOUR} 次，"
                                          "请稍后再试，或填写自己的 API Key")
            glossary_day_quota.take("all", 1, "今日全站免费额度已用完，请明天再试，或填写自己的 API Key")
        result = await generate_glossary(
            req.text, provider=provider, model=model, api_key=api_key, base_url=base_url,
            max_chars=FREE_GLOSSARY_MAX_CHARS if builtin else 200_000, **common)
        result["model"] = FREE_MODEL["label"] if req.use_free else model
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
