#!/usr/bin/env bash
# Read-only terminal monitor; never launches or restarts a crawler.
set -euo pipefail
exec "${PYTHON:-python3}" "$(dirname -- "${BASH_SOURCE[0]}")/terminal_dashboard.py" "$@"
