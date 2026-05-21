#!/bin/bash
# AI Memory System v1 — FTS5 Search
# Usage: search.sh "<query>" [--limit N] [--type feedback|project|user|reference] [--json]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="$HOME/.claude/memory-system/db/index.db"

exec python3 "${SCRIPT_DIR}/search_impl.py" "$DB_PATH" "$@"
