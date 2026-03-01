#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/venv"
REQ_FILE="$ROOT_DIR/requirements.txt"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "[INFO] project root: $ROOT_DIR"
echo "[INFO] python: $PYTHON_BIN"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[ERROR] python not found: $PYTHON_BIN"
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[INFO] creating virtualenv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
else
  echo "[INFO] reusing virtualenv: $VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[ERROR] invalid virtualenv (missing python): $VENV_DIR/bin/python"
  exit 1
fi

echo "[INFO] upgrading pip/setuptools/wheel"
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

if [[ ! -f "$REQ_FILE" ]]; then
  echo "[ERROR] requirements file not found: $REQ_FILE"
  exit 1
fi

echo "[INFO] installing dependencies from requirements.txt"
"$VENV_DIR/bin/pip" install -r "$REQ_FILE"

mkdir -p "$ROOT_DIR/.run" "$ROOT_DIR/data/images"
chmod +x "$ROOT_DIR/one-shot-startup.sh" "$ROOT_DIR/one-shot-stop.sh"

if [[ ! -x "$VENV_DIR/bin/uvicorn" ]]; then
  echo "[ERROR] setup incomplete: uvicorn not found in venv"
  exit 1
fi

echo "[OK] setup complete"
echo "Next:"
echo "  1) ./one-shot-startup.sh"
echo "  2) open http://127.0.0.1:6005"
echo "  3) ./one-shot-stop.sh"
