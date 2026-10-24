#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
if [[ ! -x .venv/bin/python ]]; then
  ./setup.sh
fi
if ! .venv/bin/python -c 'import aiohttp, playwright' 2>/dev/null; then
  printf '%s\n' 'Dependencies are missing. Run ./setup.sh first.' >&2
  exit 1
fi
exec .venv/bin/python server.py "$@"
