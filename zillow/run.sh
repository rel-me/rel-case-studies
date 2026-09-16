#!/usr/bin/env bash
# Run or resume this case study from any working directory.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

case "${1:-}" in
  -h|--help)
    cat <<'HELP'
Usage: ./run.sh [crawler options]
       ./run.sh setup
       ./run.sh inventory [options]
       ./run.sh migrate [options]

With no subcommand, run or resume the saved crawl through Release REL.app.
Example: ./run.sh --max-addresses 10000 --max-pages 20000
Use PYTHON=/path/to/python3.11 (or newer) to select the bootstrap interpreter.
HELP
    exit 0
    ;;
  setup|inventory|migrate) action="$1"; shift ;;
  *) action=run ;;
esac

if [[ ! -x .venv/bin/python || ! -f .venv/.installed || requirements.txt -nt .venv/.installed ]]; then
  "${PYTHON:-python3}" -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.installed
fi

case "$action" in
  setup) exit 0 ;;
  inventory) exec .venv/bin/python inventory.py "$@" ;;
  migrate) exec .venv/bin/python migrate.py "$@" ;;
  run) exec .venv/bin/python run.py "$@" ;;
esac
