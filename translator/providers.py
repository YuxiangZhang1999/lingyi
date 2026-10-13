"""各大模型服务商的统一流式调用接口。

- Claude 使用官方 anthropic SDK；
- GPT / Gemini / DeepSeek / Kimi / 通义千问 / 自定义服务均使用 OpenAI 兼容接口（openai SDK + base_url）。
"""
from __future__ import annotations

import ipaddress
import os
import socket
from typing import AsyncIterator
from urllib.parse import urlparse

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

# 模型列表只是网页上的“建议值”，用户可以直接输入任意模型名。
PROVIDERS: dict[str, dict] = {
    "claude": {
        "label": "Claude (Anthropic)",
        "env": "ANTHROPIC_API_KEY",
        "base_url": None,
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    },
    "openai": {
        "label": "GPT (OpenAI)",
        "env": "OPENAI_API_KEY",
        "base_url": None,
        "models": ["gpt-5", "gpt-5-mini", "gpt-4.1"],
    },
    "gemini": {
        "label": "Gemini (Google)",
        "env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "kimi": {
        "label": "Kimi (月之暗面)",
        "env": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2-0905-preview", "kimi-k2-turbo-preview", "moonshot-v1-128k"],
    },
    "qwen": {
        "label": "通义千问 (阿里云百炼)",
        "env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3-max", "qwen-plus", "qwen-turbo"],
    },
    "custom": {
        "label": "自定义（OpenAI 兼容：智谱、中转站、本地模型等）",
        "env": "CUSTOM_API_KEY",
        "base_url": os.getenv("CUSTOM_BASE_URL") or None,
        "models": [],
    },
}

# 内置免费模型：仅用于“自动生成术语表”，由服务器配置 Key，不在服务商列表中展示。
# 默认使用智谱 GLM-4.7-Flash（官方免费模型），可通过环境变量替换为其他 OpenAI 兼容的免费模型。
FREE_MODEL = {
    "env": "FREE_API_KEY",
    "base_url": os.getenv("FREE_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4/",
    "model": os.getenv("FREE_MODEL") or "glm-4.7-flash",
    "label": os.getenv("FREE_MODEL_LABEL") or "智谱 GLM-4.7-Flash",
}
PROVIDERS["free"] = {"label": FREE_MODEL["label"], "env": FREE_MODEL["env"],
                     "base_url": FREE_MODEL["base_url"], "models": [FREE_MODEL["model"]], "hidden": True}


def free_extra_body() -> dict | None:
    """默认关闭智谱模型的深度思考，抽取术语更快；不需要时可设 FREE_EXTRA_BODY={}。"""
    raw = os.getenv("FREE_EXTRA_BODY")
    if raw is None:
        return {"thinking": {"type": "disabled"}} if "bigmodel.cn" in FREE_MODEL["base_url"] else None
    import json
    return json.loads(raw) or None


# 支持服务端拒答兜底（fallbacks="default"）的 Claude 模型；Haiku 不支持。
_CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}


class ProviderError(RuntimeError):
    pass


def resolve_key(provider: str, api_key: str | None) -> str | None:
    """网页传入的 Key 优先；否则用服务器环境变量。"""
    if api_key:
        return api_key.strip()
    return os.getenv(PROVIDERS[provider]["env"]) or None


def _check_base_url(url: str) -> None:
    """公网部署时，禁止用户填写指向内网/本机的 Base URL（防止被用来探测服务器内网）。
    本机运行（HOST=127.0.0.1）或设置 ALLOW_PRIVATE_BASE_URL=1 时不限制，方便连接本地模型。"""
    if os.getenv("ALLOW_PRIVATE_BASE_URL") == "1" or os.getenv("HOST", "127.0.0.1") in ("127.0.0.1", "localhost"):
        return
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ProviderError("Base URL 格式不正确，应以 http:// 或 https:// 开头")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror:
        raise ProviderError(f"无法解析 Base URL 的域名：{u.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ProviderError("出于安全考虑，线上版本不允许 Base URL 指向内网地址")


def has_server_key(provider: str) -> bool:
    return bool(os.getenv(PROVIDERS[provider]["env"]))


async def stream_chat(
    provider: str,
    model: str,
    system: str,
    user: str,
    api_key: str | None = None,
    base_url: str | None = None,
    extra_body: dict | None = None,
) -> AsyncIterator[str]:
    """以流式方式返回模型输出的文本片段。"""
    if provider not in PROVIDERS:
        raise ProviderError(f"未知的服务商：{provider}")
    key = resolve_key(provider, api_key)
    if not key:
        raise ProviderError(f"缺少 API Key：请在网页中填写，或在服务器设置环境变量 {PROVIDERS[provider]['env']}")
    if base_url:
        _check_base_url(base_url)

    if provider == "claude":
        async for t in _stream_claude(model, system, user, key, base_url):
            yield t
    else:
        url = base_url or PROVIDERS[provider]["base_url"]
        if provider == "custom" and not url:
            raise ProviderError("自定义服务商需要填写 Base URL")
        async for t in _stream_openai_compatible(model, system, user, key, url, extra_body):
            yield t


async def _stream_claude(model, system, user, key, base_url) -> AsyncIterator[str]:
    client = AsyncAnthropic(api_key=key, base_url=base_url or None, max_retries=3)
    kwargs = dict(
        model=model,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if model in _CLAUDE_FALLBACK_MODELS:
        # 安全分类器偶发误拒时，由服务端自动换模型重试
        cm = client.beta.messages.stream(
            **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    else:
        cm = client.messages.stream(**kwargs)
    async with cm as stream:
        async for text in stream.text_stream:
            yield text
        final = await stream.get_final_message()
    if final.stop_reason == "refusal":
        raise ProviderError("模型拒绝了该段内容的翻译（refusal）")
    if final.stop_reason == "max_tokens":
        raise ProviderError("输出被截断（max_tokens），请调小分块大小")


async def _stream_openai_compatible(model, system, user, key, base_url, extra_body=None) -> AsyncIterator[str]:
    client = AsyncOpenAI(api_key=key, base_url=base_url, max_retries=3)
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        stream=True,
        extra_body=extra_body,
    )
    finish = None
    async for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.delta and choice.delta.content:
            yield choice.delta.content
        if choice.finish_reason:
            finish = choice.finish_reason
    if finish == "length":
        raise ProviderError("输出被截断（length），请调小分块大小")
    if finish == "content_filter":
        raise ProviderError("内容被服务商过滤（content_filter）")
