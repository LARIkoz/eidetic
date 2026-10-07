#!/bin/bash
# One bounded background maintenance request; never inherit Darwin background I/O.
set -euo pipefail
MEMORY_SYSTEM="${EIDETIC_MEMORY_SYSTEM:-$HOME/.claude/memory-system}"
export PATH="${PATH:-/usr/bin:/bin}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$HOME/.local/bin"
PYTHON_BIN="$(command -v python3)"
[ ! -x "$HOME/.venvs/eidetic-mlx/bin/python3" ] || PYTHON_BIN="$HOME/.venvs/eidetic-mlx/bin/python3"
# Activation comes from the user's settings/env; installing a hook must not
# turn on card mutation features that were deliberately disabled.
export EIDETIC_INDEX_LOCK_TIMEOUT=5
if [ "${1:-}" != "--locked" ]; then
    exec "$PYTHON_BIN" "$MEMORY_SYSTEM/bin/lock_runner.py" \
        "$MEMORY_SYSTEM/.m2-background.lock" /bin/bash "$0" --locked
fi
/bin/bash "$MEMORY_SYSTEM/bin/index.sh" --incremental
if [ -f "$MEMORY_SYSTEM/db/vectors.db" ]; then
    exec "$PYTHON_BIN" "$MEMORY_SYSTEM/bin/vector_maintenance.py" \
        "$MEMORY_SYSTEM/bin/embed.py" "$MEMORY_SYSTEM/db/index.db" \
        "$MEMORY_SYSTEM/db/vectors.db" "$MEMORY_SYSTEM/embed-last.log"
fi
