# 灵译

信 · 达 · 雅 —— 用大模型 API 翻译外文学术文献（英文 / 日文 / 德文为主，也支持法文、俄文、文言文等），提供网页界面和命令行两种用法。

支持的模型服务商：

- **免费**：智谱 GLM（本站内置免费额度，免填 Key 即可使用）、硅基流动、OpenRouter、Gemini（免费额度）
- **付费**：DeepSeek、Claude、GPT、Kimi、通义千问
- **自定义**：任何 OpenAI 兼容接口（中转站、本地 Ollama 等）

每个服务商都提供几款常用模型可直接选择，也可以选「其他」自己填写模型名。

## 功能

- 上传 PDF / DOCX / TXT / MD，或直接粘贴文本（扫描版或排版复杂的 PDF 建议先用 [飞桨 PaddleOCR](https://aistudio.baidu.com/paddleocr)、[MinerU](https://mineru.org.cn)、[Doc2X](https://doc2x.noedgeai.com) 或 [TextIn](https://www.textin.com) 转成 Markdown）
- PDF 自动识别页眉、页脚和页码（翻译时排除，导出时可选保留），修复连字符断词，合并跨页段落
- 按段落编号分块翻译，可并发，译文与原文逐段对齐
- 「信达雅」三档滑块调节翻译风格：信（直译）与达（意译）此消彼长，雅（润色）随意译升降；默认 信 7 · 达 4 · 雅 5（学术严谨、适度润色）
- 原文语言：英、日、德、法、俄、文言文、其他（AI 自动识别）；译为简体中文 / 繁体中文 / 英文
- 可设置学科领域、术语表、额外要求
- **AI 自动生成术语表**：拖入文本或文件（或一键从当前文献生成），自动抽取专业术语和专有名词并给出规范译名，可单独选择生成所用的服务商和模型；内置免费模型（智谱 GLM-4.7-Flash），不填 Key 也能用
- 双语对照显示，译文可直接在网页上修改
- 单块失败不影响其他部分，可一键重试失败段；中途停止后可继续
- Token 用量预估（按剩余段落、分块和术语表估算），可设置 Token 限值，达到后自动暂停；翻译时显示实际用量（服务商返回时用实际值，否则估算）
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
python translate_cli.py 論文.pdf -p deepseek -m deepseek-flash --src ja -f md
```

术语表文件每行一条，格式为 `原文 = 译文`，用 `--glossary terms.txt` 传入。

## 部署到网上

推荐海外地域（新加坡 / 东京）的云服务器：所有模型都能调用，且无需备案。注意不要选中国香港，Claude、GPT、Gemini 不对香港提供服务。

在一台全新的 Ubuntu 22.04 / 24.04 服务器上运行一条命令即可完成部署，并自动配置 HTTPS：

```bash
curl -fsSL https://raw.githubusercontent.com/YuxiangZhang1999/lingyi/main/deploy/setup.sh | sudo bash
```

- 默认使用 `服务器IP.sslip.io` 免费域名，例如 `https://43-133-1-2.sslip.io`。
- 有自己的域名时，先把域名解析（A 记录）指向服务器 IP，再运行：`curl -fsSL ...setup.sh | sudo DOMAIN=lingyi.example.com bash`
- 默认不在服务器上放任何 API Key，用户在网页中填写自己的 Key。若在 `/opt/lingyi/.env` 中填了你的 Key，**务必同时设置 `APP_PASSWORD`**，然后运行 `cd /opt/lingyi && sudo docker compose up -d`。
- 开启「智谱 GLM（免费）」免 Key 使用：到 [智谱开放平台](https://open.bigmodel.cn) 免费申请 API Key，填入 `/opt/lingyi/.env` 的 `FREE_API_KEY=`，再运行更新命令。之后用户不填 Key 也能翻译和生成术语表（仅限免费模型，按 IP 每天限额，可在 `.env` 中调整）。
- 更新到最新代码：`sudo bash /opt/lingyi/deploy/update.sh`
- 云服务器防火墙需放行 80 和 443 端口。

## 目录结构

```
app.py                 网页后端（FastAPI）
docker-compose.yml     线上部署（应用 + Caddy 自动 HTTPS）
deploy/                一键部署与更新脚本
translate_cli.py       命令行入口
static/index.html      网页前端（单文件，无需构建）
translator/
  providers.py         各模型服务商的统一调用（Claude 用 anthropic SDK，其余用 OpenAI 兼容接口）
  extract.py           PDF / DOCX / TXT 文本提取与段落整理
  core.py              分块、提示词、并发翻译、结果解析
  glossary.py          自动生成术语表（分块抽取、合并去重）
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
