#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c "import fastapi, ultralytics, PIL, scipy, celltrack" >/dev/null 2>&1; then
  echo "首次运行，正在安装本地环境..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip setuptools
  .venv/bin/python -m pip install -e .
fi

echo "Cell Tracking Studio: http://127.0.0.1:8000"
.venv/bin/python run.py &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT INT TERM
sleep 2
open "http://127.0.0.1:8000"
wait $SERVER_PID
