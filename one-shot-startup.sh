#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_UVICORN="$ROOT_DIR/venv/bin/uvicorn"
PID_DIR="$ROOT_DIR/.run"
APP_NAME="$(basename "$ROOT_DIR")"
APP_NAME_SAFE="$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-')"
PID_FILE="$PID_DIR/${APP_NAME_SAFE}.pid"
LOG_FILE="$PID_DIR/${APP_NAME_SAFE}.log"
HOST="0.0.0.0"
PORT="6005"

mkdir -p "$PID_DIR"

if [[ ! -x "$VENV_UVICORN" ]]; then
  echo "[ERROR] uvicorn not found: $VENV_UVICORN"
  echo "Run venv setup first."
  exit 1
fi

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" || true)"
  if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[INFO] already running (pid=$OLD_PID)"
    echo "URL: http://127.0.0.1:$PORT"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

# Backward compatibility: clear stale legacy pid file if present.
# If port is occupied, do not start another instance.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[ERROR] port $PORT is already in use"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  exit 1
fi

(
  cd "$ROOT_DIR"
  nohup "$VENV_UVICORN" app.main:app --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
)

sleep 1
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  echo "[OK] started (pid=$PID)"
  echo "URL: http://127.0.0.1:$PORT"
  echo "LOG: $LOG_FILE"
else
  echo "[ERROR] failed to start. check log: $LOG_FILE"
  rm -f "$PID_FILE"
  exit 1
fi
