#!/bin/bash
# AI Memory System v1 — FTS5 Indexer
# Usage: index.sh [--full|--incremental]
# Zero external deps: python3 stdlib + sqlite3
set -euo pipefail

# MLX embed engine: route python3 through the eidetic-mlx venv when present.
# Under mlx there is no onnx/CoreML → no compile-temp leak, ~18x faster embed.
[ -d "$HOME/.venvs/eidetic-mlx/bin" ] && export PATH="$HOME/.venvs/eidetic-mlx/bin:$PATH"

MODE="${1:---incremental}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -n "${EIDETIC_MEMORY_SYSTEM:-}" ]; then
    MEMORY_SYSTEM="$EIDETIC_MEMORY_SYSTEM"
elif [ -f "$INSTALLED_ROOT/.installed.json" ]; then
    MEMORY_SYSTEM="$INSTALLED_ROOT"
else
    MEMORY_SYSTEM="$HOME/.claude/memory-system"
fi
DB_PATH="$MEMORY_SYSTEM/db/index.db"
VEC_PATH="$MEMORY_SYSTEM/db/vectors.db"

python3 "${SCRIPT_DIR}/index_impl.py" "$MODE" "$DB_PATH"

# A FULL reindex rebuilds vectors too. FTS-only would leave embeddings and the
# content-hash scheme stale, and the search-time guard would then (correctly)
# degrade vector search to FTS until a real vector rebuild. The embed lock makes
# this safe against the session-start incremental embed.
# Guard: check the ACTIVE engine (mlx or fastembed), not just fastembed.
_EMBED_ENGINE=""
[ -f "$MEMORY_SYSTEM/.embed_engine" ] && _EMBED_ENGINE="$(cat "$MEMORY_SYSTEM/.embed_engine" | tr -d '[:space:]')"
[ -n "${EIDETIC_EMBED_ENGINE:-}" ] && _EMBED_ENGINE="$EIDETIC_EMBED_ENGINE"

_CAN_EMBED=0
if [ "$_EMBED_ENGINE" = "mlx" ]; then
    python3 -c "import mlx.core" >/dev/null 2>&1 && _CAN_EMBED=1
else
    python3 -c "import fastembed" >/dev/null 2>&1 && _CAN_EMBED=1
fi

if [ "$MODE" = "--full" ] && [ "$_CAN_EMBED" = "1" ]; then
    python3 "${SCRIPT_DIR}/embed.py" "$DB_PATH" "$VEC_PATH" --full
fi
