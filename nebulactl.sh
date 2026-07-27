#!/usr/bin/env bash
# Local checkout wrapper. A completed installation also provides `nebula`.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${NEBULA_PYTHON:-python3}"

if [[ $# -eq 0 ]]; then
  exec "$PYTHON" "$SCRIPT_DIR/main.py" install
fi
exec "$PYTHON" "$SCRIPT_DIR/main.py" "$@"
