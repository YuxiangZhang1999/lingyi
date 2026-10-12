# 灵译

信 · 达 · 雅 —— 用大模型 API 翻译外文学术文献（英文 / 日文 / 德文为主，也支持法文、俄文、文言文等），提供网页界面和命令行两种用法。

支持的模型服务商：Claude、GPT（OpenAI）、Gemini、DeepSeek、Kimi、通义千问，以及任何 OpenAI 兼容接口（智谱、中转站、本地 Ollama 等）。

## 功能

- 上传 PDF / DOCX / TXT / MD，或直接粘贴文本（扫描版或排版复杂的 PDF 建议先用 [飞桨 PaddleOCR](https://aistudio.baidu.com/paddleocr) 或 [MinerU](https://mineru.org.cn) 转成 Markdown）
- PDF 自动识别页眉、页脚和页码（翻译时排除，导出时可选保留），修复连字符断词，合并跨页段落
- 按段落编号分块翻译，可并发，译文与原文逐段对齐
- 「信达雅」三档滑块调节翻译风格：信（直译）与达（意译）此消彼长，雅（润色）随意译升降；默认 信 7 · 达 4 · 雅 5（学术严谨、适度润色）
- 原文语言：英、日、德、法、俄、文言文、其他（AI 自动识别）；译为简体中文 / 繁体中文 / 英文
- 可设置学科领域、术语表、额外要求
- 双语对照显示，译文可直接在网页上修改
- 单块失败不影响其他部分，可一键重试失败段；中途停止后可继续
- 进度自动保存在浏览器中，刷新页面不丢失
- 导出 Word / PDF / Markdown / 纯文本，可选双语对照或仅译文
- 上传 PDF 时可「保留原文格式」：每个原文页单独成页，保留原页眉、页脚和页码（页眉页脚保持原文不翻译）

## 快速开始（本机）

```bash
pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，填入你要用的 API Key（也可以不填，在网页上临时输入）。

```bash
python app.py
```

浏览器打开 http://127.0.0.1:8000

## 命令行用法

```bash
python translate_cli.py paper.pdf -p claude -m claude-opus-5-5 --domain 经济史 --xin 8 --da 3 --ya 4 -f pdf --preserve-layout
```

```bash
python translate_cli.py 論文.pdf -p deepseek -m deepseek-chat --src ja -f md
```

术语表文件每行一条，格式为 `原文 = 译文`，用 `--glossary terms.txt` 传入。

## 部署到网上

1. 准备一台云服务器（国内访问 Claude / GPT / Gemini 需要海外服务器或中转地址）。
2. 在 `.env` 里设置 `HOST=0.0.0.0`，并**务必设置 `APP_PASSWORD`**，否则任何人都能用你的 Key。
3. 用 Docker 运行：

```bash
docker build -t lingyi .
```

```bash
docker run -d -p 8000:8000 --env-file .env --name lingyi lingyi
```

4. 正式使用建议前面加 Nginx / Caddy 配置 HTTPS 域名。也可以部署到 Render、Railway、Fly.io 等平台（直接用这个 Dockerfile）。

## 目录结构

```
app.py                 网页后端（FastAPI）
translate_cli.py       命令行入口
static/index.html      网页前端（单文件，无需构建）
translator/
  providers.py         各模型服务商的统一调用（Claude 用 anthropic SDK，其余用 OpenAI 兼容接口）
  extract.py           PDF / DOCX / TXT 文本提取与段落整理
  core.py              分块、提示词、并发翻译、结果解析
  export.py            导出 Word / PDF / Markdown / 纯文本（可保留原文版式）
```

## 说明

- 网页上的模型列表只是建议，可以直接输入任何模型名（例如新出的模型）。
- 扫描版 PDF 没有文字层，需要先 OCR（推荐飞桨 PaddleOCR 或 MinerU，导出 Markdown 后上传）。OCR 后的 Markdown 没有页面信息，因此「保留原文格式」只对直接上传的 PDF 有效。
- 每块字符数默认 2500：调大更连贯但单次更慢，调小更稳；如果遇到“输出被截断”请调小。

## 反馈与贡献

遇到问题或有建议，欢迎在 [Issues](https://github.com/YuxiangZhang1999/lingyi/issues) 中提出，也欢迎提交 Pull Request。

## 许可证

本项目以 [GPL-3.0](LICENSE) 许可证开源。
