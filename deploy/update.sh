#!/usr/bin/env bash
# 灵译 · 拉取最新代码并重启
set -euo pipefail
cd /opt/lingyi
git pull
docker compose up -d --build
docker image prune -f >/dev/null
echo "已更新到最新版本。"
