#!/bin/bash
# VC News Platform — 서버 시작 스크립트
# 사용법: bash start_server.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PORT=8585
APP_MODULE="VC_Crawling.app:app"

echo "╔══════════════════════════════════════╗"
echo "║       VC News Platform Server        ║"
echo "║       http://0.0.0.0:${PORT}           ║"
echo "╚══════════════════════════════════════╝"

# 기존 프로세스 종료
PID=$(lsof -ti :${PORT} 2>/dev/null)
if [ -n "$PID" ]; then
    echo "→ 기존 프로세스 종료 (PID: $PID)"
    kill -9 $PID 2>/dev/null
    sleep 1
fi

cd "$PROJECT_DIR"

echo "→ 서버 시작: $APP_MODULE (포트 $PORT)"
nohup uvicorn "$APP_MODULE" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --log-level info > /tmp/vcnews.log 2>&1 &
echo "✅ VC News Platform Server started in background. (PID: $!)"
echo "Log file: /tmp/vcnews.log"
