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

# 기존 프로세스 종료 — SIGTERM 으로 정상 종료(스케줄러 shutdown 등) 유도 후
# 안 죽으면 그때만 강제(-9).
PID=$(lsof -ti :${PORT} 2>/dev/null)
if [ -n "$PID" ]; then
    echo "→ 기존 프로세스 종료 시도 (PID: $PID)"
    kill -TERM $PID 2>/dev/null
    for _ in 1 2 3 4 5; do
        kill -0 $PID 2>/dev/null || break
        sleep 1
    done
    if kill -0 $PID 2>/dev/null; then
        echo "→ 정상 종료 실패, 강제 종료(-9)"
        kill -9 $PID 2>/dev/null
        sleep 1
    fi
fi

cd "$PROJECT_DIR"

echo "→ 서버 시작: $APP_MODULE (포트 $PORT)"
nohup uvicorn "$APP_MODULE" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --log-level info > /tmp/vcnews.log 2>&1 &
echo "✅ VC News Platform Server started in background. (PID: $!)"
echo "Log file: /tmp/vcnews.log"
