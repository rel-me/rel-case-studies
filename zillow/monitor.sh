#!/usr/bin/env bash
# Terminal dashboard with explicit, confirmed crawler start/restart controls.
set -euo pipefail
exec "${PYTHON:-python3}" "$(dirname -- "${BASH_SOURCE[0]}")/terminal_dashboard.py" "$@"
