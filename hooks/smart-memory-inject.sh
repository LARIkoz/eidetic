#!/bin/bash
# AI Memory System v1 — Smart Memory Inject (SessionStart hook)
# Writes assembled memories to ~/.claude/rules/memory-context.md
# Claude auto-loads rules/ files — no 10K stdout cap.
# P3: ALL feedback rules always included. P9: only relevant, not dump.
set -euo pipefail

MEMORY_SYSTEM="${EIDETIC_MEMORY_SYSTEM:-$HOME/.claude/memory-system}"

# Capture Claude Code's session_id from the hook stdin payload BEFORE the lock
# re-exec (an exported var survives `exec env ...` and the lock_runner subprocess),
# so value-telemetry can join this SessionStart inject_log row to the SessionEnd
# session_value row. First invocation only; the non-tty guard prevents blocking on
# manual runs; fully fail-open.
if [ "${EIDETIC_LOCK_HELD:-}" != "1" ] && [ ! -t 0 ]; then
    _EIDETIC_HOOK_INPUT="$(cat 2>/dev/null || true)"
    export EIDETIC_SESSION_ID="$(printf '%s' "$_EIDETIC_HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)"
fi

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

# Incremental vector embeddings (if fastembed available).
# LOUD self-heal (W5): the 2026-06 vector outage stayed invisible for 16 days
# because this step's error went to /dev/null. Now an embed crash lands in
# embed-last.log AND surfaces a one-line warning to the session; a high vector
# lag is also flagged. A healthy, low-lag run stays silent. Still crash-guarded
# (|| true) so injection never dies, and the wrapper's own stderr is logged, not
# discarded.
VECTORS_DB="$MEMORY_SYSTEM/db/vectors.db"
EMBED_LOG="$MEMORY_SYSTEM/embed-last.log"
if [ -f "$VECTORS_DB" ]; then
    python3 - "$MEMORY_SYSTEM/bin/embed.py" "$DB" "$VECTORS_DB" "$EMBED_LOG" <<'PY' 2>>"$EMBED_LOG" || true
import os, sqlite3, subprocess, sys
script, db_path, vectors_db, log_path = sys.argv[1:5]
failed, reason = False, ""
try:
    r = subprocess.run([sys.executable, script, db_path, vectors_db],
                       timeout=30, capture_output=True, text=True)
    if r.returncode != 0:
        failed = True
        tail = (r.stderr or r.stdout or "nonzero exit").strip().splitlines()
        reason = (tail[-1] if tail else "nonzero exit")[:160]
        with open(log_path, "w") as f:
            f.write(r.stderr or r.stdout or "")
    else:
        # clean embed -> clear any stale failure marker. The W5 log is
        # failure-only; without this, one transient BrokenPipe keeps the doctor
        # "degraded" forever (alarm fatigue that erodes trust in the new gate).
        open(log_path, "w").close()
except subprocess.TimeoutExpired:
    pass  # a long reindex keeps embedding; next session resumes — not a failure
except Exception as e:
    failed = True
    reason = f"{type(e).__name__}: {e}"[:160]

# Vector REAL coverage = ALIGNED chunks (the search guard would accept), via
# coverage_audit — NOT the gross (chunks-vectors)/chunks lag, which counted dead
# orphan-vectors as coverage and hid the 99.94% chunk_id-misalignment outage.
bindir = os.path.dirname(script)
align_pct, orphan, blind = None, 0, 0
try:
    caudit = os.path.join(bindir, "coverage_audit.py")
    if os.path.exists(caudit):
        out = subprocess.run([sys.executable, caudit, db_path, vectors_db, "--oneline"],
                             timeout=15, capture_output=True, text=True)
        if out.returncode == 0:
            kv = dict(t.split("=", 1) for t in out.stdout.split() if "=" in t)
            ap = kv.get("align_pct", "")
            align_pct = int(ap) if ap.lstrip("-").isdigit() else None
            orphan = int(kv.get("orphan", "0") or "0")
            blind = int(kv.get("blind_files", "0") or "0")
except Exception:
    pass

if failed:
    print(f"⚠️  Eidetic vectors STALE — session embed failed ({reason}). "
          f"Log: {log_path} · check: bash {bindir}/doctor.sh")
elif align_pct is not None and align_pct < 80:
    # vectors EXIST but are chunk_id-misaligned -> search is blind. The exact
    # outage the old lag formula reported as "healthy". Loud.
    print(f"⚠️  Eidetic vectors {align_pct}% ALIGNED — {orphan} dead orphan-vectors, "
          f"{blind} blind files: search is BLIND despite vectors existing. "
          f"Rebuild: bash {bindir}/index.sh --full")
elif align_pct is not None and align_pct < 90:
    print(f"⚠️  Eidetic vectors {align_pct}% aligned ({blind} blind files) — self-healing. "
          f"If it persists across sessions: bash {bindir}/index.sh --full")
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
