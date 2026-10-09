#!/bin/bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I -c '
import runpy, sys
sys.path.insert(0, sys.argv.pop(1))
runpy.run_module("codex_review.claude_plan", run_name="__main__")
' "$here/src" "$@"
