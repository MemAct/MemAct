#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
memact_python="${MEMACT_PYTHON:-python3}"
if [[ ! -x .venv/bin/python ]]; then
  if ! "$memact_python" -m venv .venv; then
    printf '%s\n' 'A Python virtual environment could not be created.' 'On Fedora install python3-pip; on Debian/Ubuntu install python3-venv and python3-pip.' 'Then run ./setup.sh again. You can select a Python interpreter with MEMACT_PYTHON.' >&2
    exit 1
  fi
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
printf '%s\n' 'MemAct is ready. Run ./run.sh, then open http://127.0.0.1:8765/'
