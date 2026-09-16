#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  exec "${PYTHON:-python3}" monitor.py --help
fi
if [[ ! -x .venv/bin/python || ! -f .venv/.installed || requirements.txt -nt .venv/.installed ]]; then
  "${PYTHON:-python3}" -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.installed
fi
if [[ "${1:-}" == "setup" ]]; then exit 0; fi
exec .venv/bin/python monitor.py "$@"
