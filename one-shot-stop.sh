#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$ROOT_DIR/.run"
APP_NAME="$(basename "$ROOT_DIR")"
APP_NAME_SAFE="$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-')"
PID_FILE="$PID_DIR/${APP_NAME_SAFE}.pid"
PORT="6005"

stopped=false

for CURRENT_PID_FILE in "$PID_FILE"; do
  if [[ -f "$CURRENT_PID_FILE" ]]; then
    PID="$(cat "$CURRENT_PID_FILE" || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      kill "$PID" 2>/dev/null || true
      for _ in {1..20}; do
        if kill -0 "$PID" 2>/dev/null; then
          sleep 0.2
        else
          break
        fi
      done
      if kill -0 "$PID" 2>/dev/null; then
        kill -9 "$PID" 2>/dev/null || true
      fi
      echo "[OK] stopped pid=$PID"
      stopped=true
    fi
    rm -f "$CURRENT_PID_FILE"
  fi
done

# Fallback: stop anything still listening on 6005.
PIDS="$(lsof -t -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${PIDS:-}" ]]; then
  echo "$PIDS" | xargs kill 2>/dev/null || true
  sleep 0.5
  PIDS2="$(lsof -t -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS2:-}" ]]; then
    echo "$PIDS2" | xargs kill -9 2>/dev/null || true
  fi
  echo "[OK] cleared listeners on port $PORT"
  stopped=true
fi

if [[ "$stopped" == false ]]; then
  echo "[INFO] no running process found"
fi
