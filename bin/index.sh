#!/bin/bash
# AI Memory System v1 — FTS5 Indexer
# Usage: index.sh [--full|--incremental]
# Zero external deps: python3 stdlib + sqlite3
set -euo pipefail

MODE="${1:---incremental}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="$HOME/.claude/memory-system/db/index.db"

exec python3 "${SCRIPT_DIR}/index_impl.py" "$MODE" "$DB_PATH"
