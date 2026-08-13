#!/bin/sh

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR" || exit 1

TOOLS_DIR="$SCRIPT_DIR/.tools/uv"
UV_BIN="$TOOLS_DIR/uv"
LOG_DIR="$SCRIPT_DIR/workspace/logs"
BOOTSTRAP_LOG="$LOG_DIR/bootstrap.log"
UV_VERSION="0.11.16"

mkdir -p "$TOOLS_DIR" "$LOG_DIR"

log() {
  printf '%s\n' "$1"
  printf '%s\n' "$1" >>"$BOOTSTRAP_LOG"
}

fail() {
  log ""
  log "启动准备失败：$1"
  log "详细日志：$BOOTSTRAP_LOG"
  exit 1
}

run_logged() {
  "$@" >>"$BOOTSTRAP_LOG" 2>&1
}

: >"$BOOTSTRAP_LOG"
log "[1/4] 检查启动工具"

if [ ! -x "$UV_BIN" ]; then
  log "首次安装需要联网，通常需要数分钟，并可能下载数 GB 的科学计算依赖。"
  INSTALLER_URL="https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/uv-installer.sh"
  if command -v curl >/dev/null 2>&1; then
    if ! curl -LsSf "$INSTALLER_URL" -o "$TOOLS_DIR/install.sh"; then
      fail "首次安装需要联网。请检查网络连接后重新双击启动。"
    fi
  elif command -v wget >/dev/null 2>&1; then
    if ! wget -q "$INSTALLER_URL" -O "$TOOLS_DIR/install.sh"; then
      fail "首次安装需要联网。请检查网络连接后重新双击启动。"
    fi
  else
    fail "系统缺少下载工具 curl 或 wget。请联系技术人员。"
  fi

  if ! UV_UNMANAGED_INSTALL="$TOOLS_DIR" sh "$TOOLS_DIR/install.sh" >>"$BOOTSTRAP_LOG" 2>&1; then
    fail "启动工具安装失败。请检查网络连接后重新双击启动。"
  fi
fi

log "[2/4] 准备 Python 3.11"
if ! run_logged "$UV_BIN" python install 3.11; then
  fail "Python 3.11 准备失败。请检查网络连接后重新双击启动。"
fi

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
NEEDS_REBUILD=0
if [ -e "$SCRIPT_DIR/.venv" ]; then
  if [ ! -x "$VENV_PYTHON" ]; then
    NEEDS_REBUILD=1
  elif ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1; then
    NEEDS_REBUILD=1
  fi
fi

if [ "$NEEDS_REBUILD" -eq 1 ]; then
  log "检测到不完整或来自其他电脑的软件环境，正在自动修复..."
  if ! run_logged "$UV_BIN" venv --clear --python 3.11 --managed-python "$SCRIPT_DIR/.venv"; then
    fail "损坏的软件环境无法自动修复。请将日志发送给技术人员。"
  fi
fi

log "[3/4] 安装或检查软件依赖"
if ! run_logged "$UV_BIN" sync --locked --python 3.11 --managed-python; then
  fail "软件环境安装失败。无需手动处理 Python，请重新双击启动；如果仍然失败，请将日志发送给技术人员。"
fi

if ! "$VENV_PYTHON" -c 'import fastapi, uvicorn, ultralytics, PIL, numpy, scipy, matplotlib, celltrack' >>"$BOOTSTRAP_LOG" 2>&1; then
  log "依赖检查失败，正在重建软件环境..."
  if ! run_logged "$UV_BIN" venv --clear --python 3.11 --managed-python "$SCRIPT_DIR/.venv"; then
    fail "软件环境重建失败。请将日志发送给技术人员。"
  fi
  if ! run_logged "$UV_BIN" sync --locked --python 3.11 --managed-python; then
    fail "软件环境重建后仍无法安装依赖。请将日志发送给技术人员。"
  fi
fi

log "[4/4] 启动 Cell Tracking Studio"
exec "$VENV_PYTHON" "$SCRIPT_DIR/scripts/launcher.py" "$@"
