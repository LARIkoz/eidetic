#!/bin/bash
# AI Memory System v2 — Cleanup
# Usage: cleanup.sh [--report|--archive [N]]
set -euo pipefail
exec python3 "$(dirname "$0")/cleanup.py" "$@"
