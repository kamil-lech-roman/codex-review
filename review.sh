#!/bin/bash
# Argv and exit codes only (§2). All behaviour lives in the Python driver.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$here/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m codex_review "$@"
