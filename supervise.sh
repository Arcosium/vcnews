#!/bin/bash
# VC News Platform — watchdog. Keeps the uvicorn server alive.
# Run once in the background:  nohup /home/opc/projects/VC_Crawling/supervise.sh > /tmp/vcnews_supervise.log 2>&1 &
set -u
APP_DIR="/home/opc/projects/VC_Crawling"
PORT=8585
APP_LOG="/tmp/vcnews.log"
APP_MODULE="VC_Crawling.app:app"

start_server() {
  echo "$(date -u +%FT%TZ) [supervise] starting uvicorn on :$PORT"
  ( cd "$APP_DIR/.." && \
    nohup uvicorn "$APP_MODULE" --host 0.0.0.0 --port $PORT >> "$APP_LOG" 2>&1 & )
}

server_up() { curl -fsS -m4 "http://localhost:$PORT/api/settings" >/dev/null 2>&1; }

while true; do
  server_up || { echo "$(date -u +%FT%TZ) [supervise] server DOWN → restart"; start_server; sleep 5; }
  sleep 20
done
