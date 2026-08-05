#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

IFACE=$(route get default 2>/dev/null | awk '/interface:/{print $2}')
LAN_IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null || echo "取得できませんでした")

echo ""
echo "同じWi-FiのiPad/iPhoneから以下にアクセス:"
echo "  http://${LAN_IP}:8000"
echo ""
echo "終了するには Ctrl+C を押してください"
echo ""

cd backend
exec ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
