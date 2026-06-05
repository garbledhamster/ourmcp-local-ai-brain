#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Python 3.10+ is required for Local AI Brain. Install Python or run through a host that provides a Python runtime." >&2
    exit 1
  fi
fi
if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
else
  export PYTHONPATH="$SCRIPT_DIR"
fi

exec "$PYTHON_BIN" -m local_ai_brain "$@"
