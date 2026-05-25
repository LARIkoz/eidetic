#!/bin/bash
# AI Memory System v1 — Smart Memory Inject (SessionStart hook)
# Writes assembled memories to ~/.claude/rules/memory-context.md
# Claude auto-loads rules/ files — no 10K stdout cap.
# P3: ALL feedback rules always included. P9: only relevant, not dump.
set -euo pipefail

MEMORY_SYSTEM="${EIDETIC_MEMORY_SYSTEM:-$HOME/.claude/memory-system}"

if [ "${EIDETIC_LOCK_HELD:-}" != "1" ] && [ -f "$MEMORY_SYSTEM/bin/lock_runner.py" ]; then
    exec env EIDETIC_LOCK_HELD=1 python3 "$MEMORY_SYSTEM/bin/lock_runner.py" "$MEMORY_SYSTEM/.memory.lockfile" "$0" "$@"
fi

acquire_memory_lock() {
    if [ "${EIDETIC_LOCK_HELD:-}" = "1" ]; then
        return 0
    fi
    local lockdir="$MEMORY_SYSTEM/.memory.lock"
    mkdir -p "$MEMORY_SYSTEM"
    if mkdir "$lockdir" 2>/dev/null; then
        printf '%s\n' "$$" > "$lockdir/pid"
        trap 'rm -rf "$MEMORY_SYSTEM/.memory.lock"' EXIT
        return 0
    fi

    local old_pid=""
    old_pid=$(cat "$lockdir/pid" 2>/dev/null || true)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "Memory system busy (PID $old_pid alive), skipping"
        return 1
    fi
    echo "Memory system lock is stale or malformed; remove $lockdir manually after verifying no hook is running"
    return 1
}

acquire_memory_lock || exit 0

DB="$MEMORY_SYSTEM/db/index.db"
RULES_FILE="$HOME/.claude/rules/memory-context.md"
SEARCH="$MEMORY_SYSTEM/bin/search.sh"
INDEX="$MEMORY_SYSTEM/bin/index.sh"

find_project_memory_fallback() {
    local sanitized project_dir project_name candidate suffix
    sanitized="$(pwd)"
    sanitized="${sanitized%/}"
    sanitized="${sanitized//\//-}"
    sanitized="${sanitized#-}"

    for project_dir in "$HOME"/.claude/projects/*; do
        [ -d "$project_dir" ] || continue
        project_name="$(basename "$project_dir")"
        candidate="$project_dir/memory/MEMORY.md"
        [ -f "$candidate" ] || continue
        if [ "$project_name" = "$sanitized" ] || [ "$project_name" = "-$sanitized" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    for project_dir in "$HOME"/.claude/projects/*; do
        [ -d "$project_dir" ] || continue
        project_name="$(basename "$project_dir")"
        candidate="$project_dir/memory/MEMORY.md"
        [ -f "$candidate" ] || continue
        suffix="${project_name#-}"
        if [ "${#suffix}" -gt 10 ] && [[ "$sanitized" = *"$suffix" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

# Fallback: if memory system is not ready, use only the CWD-matching project MEMORY.md.
if [ ! -f "$DB" ] || [ ! -f "$SEARCH" ]; then
    fallback_memory="$(find_project_memory_fallback || true)"
    if [ -n "$fallback_memory" ]; then
        mkdir -p "$(dirname "$RULES_FILE")"
        head -200 "$fallback_memory" > "$RULES_FILE" 2>/dev/null || true
        echo "Memory context: fallback to project MEMORY.md (index not ready)"
        exit 0
    fi
    echo "Memory context: no index, no project MEMORY.md"
    exit 0
fi

# Incremental reindex (< 500ms)
"$INDEX" --incremental >/dev/null 2>&1 || true

# Drift detection (24h throttle, crash-guarded — B6: must not kill injection)
python3 "$MEMORY_SYSTEM/bin/drift_check.py" "$DB" 2>/dev/null || true

# Code indexing for CWD project (if tree-sitter available)
if python3 -c "import tree_sitter" 2>/dev/null; then
    python3 - "$MEMORY_SYSTEM/bin/code_index.py" "$DB" "$(pwd)" <<'PY' 2>/dev/null || true
import subprocess, sys
script, db_path, project_dir = sys.argv[1:4]
try:
    subprocess.run([sys.executable, script, db_path, project_dir],
                   timeout=10, capture_output=True)
except subprocess.TimeoutExpired:
    pass
PY
fi

# Incremental vector embeddings (if fastembed available)
VECTORS_DB="$MEMORY_SYSTEM/db/vectors.db"
if [ -f "$VECTORS_DB" ]; then
    python3 - "$MEMORY_SYSTEM/bin/embed.py" "$DB" "$VECTORS_DB" <<'PY' 2>/dev/null || true
import subprocess, sys
script, db_path, vectors_db = sys.argv[1:4]
try:
    subprocess.run([sys.executable, script, db_path, vectors_db],
                   timeout=30, capture_output=True)
except subprocess.TimeoutExpired:
    pass
PY
fi

# Record session + get phase-adaptive hint
SESSION_HINT=$(python3 "$MEMORY_SYSTEM/bin/session_counter.py" "$(pwd)" "record-and-hint" 2>/dev/null || echo "")

# Assemble context via Python (crash-guarded — B6/H4: must not kill injection)
python3 "$MEMORY_SYSTEM/bin/assemble_context.py" "$DB" "$RULES_FILE" "$(pwd)" || true

# Append session hint to memory-context.md
if [ -n "$SESSION_HINT" ] && [ -f "$RULES_FILE" ]; then
    echo "" >> "$RULES_FILE"
    echo "## Session Awareness" >> "$RULES_FILE"
    echo "" >> "$RULES_FILE"
    echo "$SESSION_HINT" >> "$RULES_FILE"
fi

# Check for updates (background, non-blocking)
UPDATE_CHECK="$MEMORY_SYSTEM/bin/check-update.sh"
if [ -f "$UPDATE_CHECK" ]; then
    bash "$UPDATE_CHECK" 2>/dev/null &
fi

echo "Memory context updated (with session hint)"
