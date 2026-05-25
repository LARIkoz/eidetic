#!/bin/bash
# Eidetic — check for updates (runs at SessionStart, fast + non-blocking)
# Writes a one-line notice to stdout if update available. Silent otherwise.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALLED_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -n "${EIDETIC_MEMORY_SYSTEM:-}" ]; then
    MEMORY_SYSTEM="$EIDETIC_MEMORY_SYSTEM"
elif [ -f "$INSTALLED_ROOT/.installed.json" ]; then
    MEMORY_SYSTEM="$INSTALLED_ROOT"
else
    MEMORY_SYSTEM="$HOME/.claude/memory-system"
fi
META="$MEMORY_SYSTEM/.installed.json"
UPDATE_MARKER="$MEMORY_SYSTEM/.update-available"
REPO="https://github.com/LARIkoz/eidetic.git"

if [ ! -f "$META" ]; then
    exit 0
fi

LOCAL_SHA=$(
python3 - "$META" 2>/dev/null << 'PYEOF' || echo ""
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("git_sha", ""))
PYEOF
)
if [ -z "$LOCAL_SHA" ]; then
    exit 0
fi

LAST_CHECK=$(
python3 - "$META" 2>/dev/null << 'PYEOF' || echo "check"
import json, time
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    m = json.load(f)
lc = m.get("last_update_check", 0)
print('skip' if time.time() - lc < 21600 else 'check')
PYEOF
)

if [ "$LAST_CHECK" = "skip" ]; then
    if [ -f "$UPDATE_MARKER" ]; then
        cat "$UPDATE_MARKER"
    fi
    exit 0
fi

REMOTE_SHA=$(git ls-remote "$REPO" refs/heads/main 2>/dev/null | cut -f1 || echo "")

python3 - "$META" << 'PYEOF' 2>/dev/null || true
import json, time
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    m = json.load(f)
m["last_update_check"] = time.time()
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(m, f, indent=2)
PYEOF

if [ -z "$REMOTE_SHA" ]; then
    exit 0
fi

if [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
    COMMAND_MEMORY_SYSTEM=$(printf '%q' "$MEMORY_SYSTEM")
    MSG="Eidetic update available (${REMOTE_SHA:0:7}). Run: bash $COMMAND_MEMORY_SYSTEM/bin/update.sh"
    echo "$MSG" > "$UPDATE_MARKER"
    echo "$MSG"
else
    rm -f "$UPDATE_MARKER"
fi
