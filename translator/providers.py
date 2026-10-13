"""各大模型服务商的统一流式调用接口。

- Claude 使用官方 anthropic SDK；
- 其余服务商（智谱、硅基流动、OpenRouter、GPT、Gemini、DeepSeek、Kimi、通义千问、自定义）均使用 OpenAI 兼容接口（openai SDK + base_url）。
"""
from __future__ import annotations

import ipaddress
import os
import socket
from typing import AsyncIterator
from urllib.parse import urlparse

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

# 每个服务商的常用模型（[模型名, 说明]），网页上作为下拉选项；用户也可以选“其他”自己填写。
# 模型更新很快，这里的列表核对于 2026 年 10 月，以各家官网为准。
FREE_ZHIPU_MODELS = ["glm-4.7-flash", "glm-4-flash-250414"]  # 智谱官方免费模型

PROVIDERS: dict[str, dict] = {
    # ---------- 免费服务商 ----------
    "zhipu": {
        "label": "智谱 GLM（免费）",
        "env": "FREE_API_KEY",      # 服务器内置 Key：只允许调用免费模型，并按 IP 限额
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "builtin_models": FREE_ZHIPU_MODELS,
        "key_url": "https://open.bigmodel.cn",
        "models": [
            ["glm-4.7-flash", "免费 · 推荐"],
            ["glm-4-flash-250414", "免费"],
            ["glm-5.2", "旗舰 · 需自己的 Key"],
            ["glm-4.7", "需自己的 Key"],
        ],
    },
    "siliconflow": {
        "label": "硅基流动（免费）",
        "env": "SILICONFLOW_API_KEY",
        "base_url": "https://api.siliconflow.cn/v1",
        "key_url": "https://cloud.siliconflow.cn",
        "models": [
            ["Qwen/Qwen3-8B", "免费"],
            ["THUDM/GLM-4-9B-0414", "免费"],
            ["Qwen/Qwen2.5-7B-Instruct", "免费"],
        ],
    },
    "openrouter": {
        "label": "OpenRouter（免费）",
        "env": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "key_url": "https://openrouter.ai/keys",
        "models": [
            ["qwen/qwen3.8-27b:free", "免费"],
            ["google/gemma-4-31b-it:free", "免费"],
            ["nvidia/nemotron-3-super-120b-a12b:free", "免费"],
            ["openrouter/free", "免费 · 自动选择"],
        ],
    },
    "gemini": {
        "label": "Gemini（免费额度）",
        "env": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_url": "https://aistudio.google.com/apikey",
        "models": [
            ["gemini-3-flash-preview", "有免费额度"],
            ["gemini-3.1-flash-lite", "轻量"],
            ["gemini-3.1-pro-preview", "旗舰 · 付费"],
        ],
    },
    # ---------- 付费服务商 ----------
    "deepseek": {
        "label": "DeepSeek",
        "env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "key_url": "https://platform.deepseek.com/api_keys",
        "models": [["deepseek-v4-pro", "旗舰"], ["deepseek-flash", "快速 · 便宜"]],
    },
    "claude": {
        "label": "Claude (Anthropic)",
        "env": "ANTHROPIC_API_KEY",
        "base_url": None,
        "key_url": "https://console.anthropic.com/settings/keys",
        "models": [
            ["claude-opus-5-5", "推荐"],
            ["claude-sonnet-5-5", "均衡"],
            ["claude-haiku-5-5", "快速 · 便宜"],
            ["claude-fable-5-1", "最强"],
        ],
    },
    "openai": {
        "label": "GPT (OpenAI)",
        "env": "OPENAI_API_KEY",
        "base_url": None,
        "key_url": "https://platform.openai.com/api-keys",
        "models": [["gpt-5.5", "旗舰"], ["gpt-5.4-mini", "均衡"], ["gpt-5.4-nano", "快速 · 便宜"]],
    },
    "kimi": {
        "label": "Kimi (月之暗面)",
        "env": "MOONSHOT_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
        "key_url": "https://platform.kimi.com",
        "models": [["kimi-k3", "旗舰"], ["kimi-k2.6", "均衡"]],
    },
    "qwen": {
        "label": "通义千问 (阿里云百炼)",
        "env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_url": "https://bailian.console.aliyun.com",
        "models": [
            ["qwen3.7-max", "旗舰"],
            ["qwen3.8-flash", "快速 · 便宜"],
            ["qwen3.7-flash", "快速"],
            ["qwen-plus", "均衡"],
        ],
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "env": "CUSTOM_API_KEY",
        "base_url": os.getenv("CUSTOM_BASE_URL") or None,
        "models": [],
    },
}
DEFAULT_PROVIDER = "zhipu"

# “自动生成术语表”使用的内置免费模型
FREE_MODEL = {"provider": "zhipu", "model": os.getenv("FREE_MODEL") or "glm-4.7-flash",
              "label": os.getenv("FREE_MODEL_LABEL") or "智谱 GLM-4.7-Flash"}


def model_ids(provider: str) -> list[str]:
    return [m[0] for m in PROVIDERS[provider]["models"]]


def default_extra_body(provider: str, model: str) -> dict | None:
    """智谱 GLM-4.5 及以后的模型默认开启深度思考，翻译和抽取术语时关闭，速度更快。"""
    if provider == "zhipu" and model.startswith(("glm-4.5", "glm-4.6", "glm-4.7", "glm-5")):
        return {"thinking": {"type": "disabled"}}
    return None


def uses_builtin_key(provider: str, api_key: str | None) -> bool:
    """用户没填 Key、且该服务商由服务器提供内置免费额度时为 True（需要限额）。"""
    return not api_key and "builtin_models" in PROVIDERS.get(provider, {})


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
    if uses_builtin_key(provider, api_key) and model not in PROVIDERS[provider]["builtin_models"]:
        raise ProviderError(f"本站免费额度只能使用免费模型（{'、'.join(PROVIDERS[provider]['builtin_models'])}），"
                            "其他模型请填写你自己的 API Key")
    key = resolve_key(provider, api_key)
    if not key:
        raise ProviderError("缺少 API Key：请在左侧填写该服务商的 API Key")
    if base_url:
        _check_base_url(base_url)

    if provider == "claude":
        async for t in _stream_claude(model, system, user, key, base_url):
            yield t
    else:
        url = base_url or PROVIDERS[provider]["base_url"]
        if provider == "custom" and not url:
            raise ProviderError("自定义服务商需要填写 Base URL")
        if extra_body is None:
            extra_body = default_extra_body(provider, model)
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
