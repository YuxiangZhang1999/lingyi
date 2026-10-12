#!/usr/bin/env bash
# 灵译 · 一键部署脚本（Ubuntu 22.04 / 24.04）
# 用法：curl -fsSL https://raw.githubusercontent.com/YuxiangZhang1999/lingyi/main/deploy/setup.sh | sudo bash
# 绑定自己的域名：... | sudo DOMAIN=lingyi.example.com bash
set -euo pipefail

REPO="https://github.com/YuxiangZhang1999/lingyi.git"
DIR="/opt/lingyi"

[ "$(id -u)" -eq 0 ] || { echo "请用 sudo 运行"; exit 1; }

echo "==> 1/4 安装 Docker 和 Git"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates docker.io >/dev/null
apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 || apt-get install -y -qq docker-compose-plugin >/dev/null
systemctl enable --now docker >/dev/null

echo "==> 2/4 下载代码"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
cd "$DIR"

echo "==> 3/4 生成配置"
IP="$(curl -fsS4 --max-time 10 https://api.ipify.org || curl -fsS4 --max-time 10 https://ifconfig.me)"
# 没有自己的域名时，用 sslip.io 免费域名（如 1-2-3-4.sslip.io 会解析到 1.2.3.4），这样也能拿到 HTTPS 证书
SITE="${DOMAIN:-${IP//./-}.sslip.io}"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env：默认不在服务器上放任何 API Key，用户在网页中填写自己的 Key。"
fi
sed -i '/^SITE_ADDRESS=/d; /^HOST=/d' .env
echo "SITE_ADDRESS=$SITE" >> .env

echo "==> 4/4 构建并启动（首次约需 2-5 分钟）"
docker compose up -d --build

echo
echo "部署完成！访问地址：https://$SITE"
echo "（首次访问时会自动申请 HTTPS 证书，可能要等几十秒；打不开请检查云服务器防火墙是否放行了 80 和 443 端口）"
echo "以后更新代码：sudo bash $DIR/deploy/update.sh"
