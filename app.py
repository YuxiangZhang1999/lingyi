"""灵译 · 网页服务入口。启动：python app.py  然后浏览器打开 http://127.0.0.1:8000"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, Response, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from translator.core import DEFAULT_DA, DEFAULT_XIN, DEFAULT_YA, Item, Settings, translate_items  # noqa: E402
from translator.export import FORMATS, export  # noqa: E402
from translator.extract import extract  # noqa: E402
from translator.providers import PROVIDERS, has_server_key  # noqa: E402

BASE = Path(__file__).parent
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

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
            for k, v in PROVIDERS.items()
        },
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
    items = [Item(int(x["id"]), str(x["text"])) for x in req.items if str(x.get("text", "")).strip()]
    if not items:
        raise HTTPException(400, "没有需要翻译的内容")
    s = Settings(**req.model_dump(exclude={"items"}))

    async def gen():
        # NDJSON：每行一个 JSON 事件，前端边收边显示
        async for ev in translate_items(items, s):
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


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
