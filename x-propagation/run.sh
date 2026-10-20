#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ "${1:-}" == setup ]]; then
  "${PYTHON:-python3}" -m venv .venv
  .venv/bin/python -m pip install -r requirements.lock.txt
  exit 0
fi
exec .venv/bin/python collector.py "$@"
