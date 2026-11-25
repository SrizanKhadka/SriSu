#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Python 3 is required to start the SriSu local server." >&2
    exit 1
fi

exec "$PYTHON" "$ROOT_DIR/tools/start_local_server.py" "$@"
