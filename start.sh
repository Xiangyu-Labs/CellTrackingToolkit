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
  log "Startup preparation failed: $1"
  log "Detailed log: $BOOTSTRAP_LOG"
  exit 1
}

run_logged() {
  "$@" >>"$BOOTSTRAP_LOG" 2>&1
}

: >"$BOOTSTRAP_LOG"
log "[1/6] Checking startup tools"

if [ ! -x "$UV_BIN" ]; then
  log "An internet connection is required for the first installation. The process usually takes several minutes and may download several GB of scientific computing dependencies."
  INSTALLER_URL="https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/uv-installer.sh"
  if command -v curl >/dev/null 2>&1; then
    if ! curl -LsSf "$INSTALLER_URL" -o "$TOOLS_DIR/install.sh"; then
      fail "An internet connection is required for the first installation. Check your connection and start the application again."
    fi
  elif command -v wget >/dev/null 2>&1; then
    if ! wget -q "$INSTALLER_URL" -O "$TOOLS_DIR/install.sh"; then
      fail "An internet connection is required for the first installation. Check your connection and start the application again."
    fi
  else
    fail "The system does not have the curl or wget download tool. Contact technical support."
  fi

  if ! UV_UNMANAGED_INSTALL="$TOOLS_DIR" sh "$TOOLS_DIR/install.sh" >>"$BOOTSTRAP_LOG" 2>&1; then
    fail "The startup tool could not be installed. Check your connection and start the application again."
  fi
fi

log "[2/6] Preparing Python 3.11"
if ! run_logged "$UV_BIN" python install 3.11; then
  fail "Python 3.11 could not be prepared. Check your connection and start the application again."
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
  log "An incomplete or transferred application environment was detected. Repairing it automatically..."
  if ! run_logged "$UV_BIN" venv --clear --python 3.11 --managed-python "$SCRIPT_DIR/.venv"; then
    fail "The damaged application environment could not be repaired automatically. Send the log to technical support."
  fi
fi

if [ "${CELLTRACK_UPDATE_RESTARTED:-0}" != "1" ]; then
  log "[3/6] Checking for application updates"
  "$UV_BIN" run --python 3.11 --no-project \
    "$SCRIPT_DIR/scripts/updater.py" --app-only >>"$BOOTSTRAP_LOG" 2>&1
  UPDATE_STATUS=$?
  if [ "$UPDATE_STATUS" -eq 10 ]; then
    log "Restarting with the updated application..."
    CELLTRACK_UPDATE_RESTARTED=1
    export CELLTRACK_UPDATE_RESTARTED
    exec "$SCRIPT_DIR/start.sh" "$@"
  elif [ "$UPDATE_STATUS" -ne 0 ]; then
    log "Automatic update check failed; continuing with this version."
  fi
else
  log "[3/6] Application update applied"
fi

log "[4/6] Installing or checking application dependencies"
if ! run_logged "$UV_BIN" sync --locked --python 3.11 --managed-python; then
  fail "The application environment could not be installed. Do not modify Python manually; start the application again, and send the log to technical support if it still fails."
fi

if ! "$VENV_PYTHON" -c 'import fastapi, uvicorn, ultralytics, PIL, numpy, scipy, matplotlib, celltrack' >>"$BOOTSTRAP_LOG" 2>&1; then
  log "The dependency check failed. Rebuilding the application environment..."
  if ! run_logged "$UV_BIN" venv --clear --python 3.11 --managed-python "$SCRIPT_DIR/.venv"; then
    fail "The application environment could not be rebuilt. Send the log to technical support."
  fi
  if ! run_logged "$UV_BIN" sync --locked --python 3.11 --managed-python; then
    fail "Dependencies still could not be installed after rebuilding the application environment. Send the log to technical support."
  fi
fi

log "[5/6] Checking the segmentation model"
if ! run_logged "$VENV_PYTHON" "$SCRIPT_DIR/scripts/updater.py" --model-only; then
  fail "The segmentation model could not be downloaded. Check the internet connection and start the application again."
fi

log "[6/6] Starting Cell Tracking Studio"
exec "$VENV_PYTHON" "$SCRIPT_DIR/scripts/launcher.py" "$@"
