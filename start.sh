#!/usr/bin/env bash
# Start, stop or check VIGIL (go2rtc, backend, frontend) on a Linux host.
#
#   ./start.sh            start everything (same as "start")
#   ./start.sh stop       stop everything
#   ./start.sh restart    stop, then start
#   ./start.sh status     show what is running
#   ./start.sh logs       show the last lines of each log
#
# Backend and frontend run in tmux sessions named "backend" and "frontend"
# (attach with: tmux attach -t backend). go2rtc runs in the background.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
GO2RTC_BIN="$ROOT/go2rtc-bin"
GO2RTC_URL="https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64"

log() { echo "[vigil] $*"; }

load_env() {
  if [[ ! -f .env ]]; then
    echo "Missing $ROOT/.env - copy .env.example to .env and fill it in." >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  for key in FERNET_KEY JWT_SECRET_KEY; do
    if [[ -z "${!key:-}" ]]; then
      echo "$key is empty in .env" >&2
      exit 1
    fi
  done
}

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

start_go2rtc() {
  if [[ ! -x "$GO2RTC_BIN" ]]; then
    log "downloading go2rtc"
    curl -fL -o "$GO2RTC_BIN" "$GO2RTC_URL"
    chmod +x "$GO2RTC_BIN"
  fi
  pkill -x go2rtc-bin 2>/dev/null || true
  sleep 1
  nohup "$GO2RTC_BIN" -config go2rtc/go2rtc.yaml > go2rtc.log 2>&1 &
  log "go2rtc started (log: go2rtc.log)"
}

start_backend() {
  if [[ ! -x .venv/bin/uvicorn ]]; then
    log "creating virtualenv and installing backend dependencies"
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r backend/requirements.txt
  fi
  tmux kill-session -t backend 2>/dev/null || true
  # The tmux server inherits this environment, so the exported .env values reach uvicorn.
  tmux new -d -s backend \
    "cd '$ROOT' && source .venv/bin/activate && uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $BACKEND_PORT 2>&1 | tee backend.log"
  log "backend started on 127.0.0.1:$BACKEND_PORT (tmux: backend)"
}

start_frontend() {
  if [[ ! -d frontend/node_modules ]]; then
    log "installing frontend dependencies"
    (cd frontend && npm install)
  fi
  tmux kill-session -t frontend 2>/dev/null || true
  tmux new -d -s frontend \
    "cd '$ROOT/frontend' && npm run dev -- --host 0.0.0.0 --port $FRONTEND_PORT 2>&1 | tee ../frontend.log"
  log "frontend started on 0.0.0.0:$FRONTEND_PORT (tmux: frontend)"
}

stop_all() {
  tmux kill-session -t frontend 2>/dev/null || true
  tmux kill-session -t backend 2>/dev/null || true
  pkill -x go2rtc-bin 2>/dev/null || true
  log "stopped"
}

show_status() {
  ss -ltnp 2>/dev/null | grep -E ":($FRONTEND_PORT|$BACKEND_PORT|1984|8554)\b" || log "nothing is listening"
  tmux ls 2>/dev/null || true
  curl -s -m 3 "localhost:$BACKEND_PORT/health" || true
  echo
  curl -s -m 3 -o /dev/null -w "frontend %{http_code}\n" "localhost:$FRONTEND_PORT" || true
  curl -s -m 3 -o /dev/null -w "go2rtc %{http_code}\n" "localhost:1984" || true
}

show_logs() {
  for f in go2rtc.log backend.log frontend.log; do
    echo "=== $f ==="
    tail -n 15 "$f" 2>/dev/null || echo "(no log yet)"
  done
}

case "${1:-start}" in
  start)
    need tmux; need curl; need npm; need python3
    load_env
    start_go2rtc
    start_backend
    start_frontend
    log "waiting for services"
    sleep 20
    show_status
    ;;
  stop) stop_all ;;
  restart) "$0" stop; "$0" start ;;
  status) show_status ;;
  logs) show_logs ;;
  *) echo "Usage: $0 {start|stop|restart|status|logs}" >&2; exit 1 ;;
esac
