"""各大模型服务商的统一流式调用接口。

- Claude 使用官方 anthropic SDK；
- GPT / Gemini / DeepSeek / Kimi / 通义千问 / 自定义服务均使用 OpenAI 兼容接口（openai SDK + base_url）。
"""
from __future__ import annotations

import os
from typing import AsyncIterator

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

# 支持服务端拒答兜底（fallbacks="default"）的 Claude 模型；Haiku 不支持。
_CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}


class ProviderError(RuntimeError):
    pass


def resolve_key(provider: str, api_key: str | None) -> str | None:
    """网页传入的 Key 优先；否则用服务器环境变量。"""
    if api_key:
        return api_key.strip()
    return os.getenv(PROVIDERS[provider]["env"]) or None


def has_server_key(provider: str) -> bool:
    return bool(os.getenv(PROVIDERS[provider]["env"]))


async def stream_chat(
    provider: str,
    model: str,
    system: str,
    user: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AsyncIterator[str]:
    """以流式方式返回模型输出的文本片段。"""
    if provider not in PROVIDERS:
        raise ProviderError(f"未知的服务商：{provider}")
    key = resolve_key(provider, api_key)
    if not key:
        raise ProviderError(f"缺少 API Key：请在网页中填写，或在服务器设置环境变量 {PROVIDERS[provider]['env']}")

    if provider == "claude":
        async for t in _stream_claude(model, system, user, key, base_url):
            yield t
    else:
        url = base_url or PROVIDERS[provider]["base_url"]
        if provider == "custom" and not url:
            raise ProviderError("自定义服务商需要填写 Base URL")
        async for t in _stream_openai_compatible(model, system, user, key, url):
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


async def _stream_openai_compatible(model, system, user, key, base_url) -> AsyncIterator[str]:
    client = AsyncOpenAI(api_key=key, base_url=base_url, max_retries=3)
    stream = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        stream=True,
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
