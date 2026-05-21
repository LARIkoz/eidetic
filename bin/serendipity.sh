#!/bin/bash
# AI Memory System — Serendipity Links
# Usage: serendipity.sh "<query>"
set -euo pipefail
exec python3 "$(dirname "$0")/serendipity.py" "$@"
