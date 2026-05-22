#!/bin/bash
# Eidetic — check for updates (runs at SessionStart, fast + non-blocking)
# Writes a one-line notice to stdout if update available. Silent otherwise.
set -euo pipefail

MEMORY_SYSTEM="$HOME/.claude/memory-system"
META="$MEMORY_SYSTEM/.installed.json"
UPDATE_MARKER="$MEMORY_SYSTEM/.update-available"
REPO="https://github.com/LARIkoz/eidetic.git"

if [ ! -f "$META" ]; then
    exit 0
fi

LOCAL_SHA=$(python3 -c "import json; print(json.load(open('$META')).get('git_sha',''))" 2>/dev/null || echo "")
if [ -z "$LOCAL_SHA" ]; then
    exit 0
fi

LAST_CHECK=$(python3 -c "
import json, time
m = json.load(open('$META'))
lc = m.get('last_update_check', 0)
print('skip' if time.time() - lc < 21600 else 'check')
" 2>/dev/null || echo "check")

if [ "$LAST_CHECK" = "skip" ]; then
    if [ -f "$UPDATE_MARKER" ]; then
        cat "$UPDATE_MARKER"
    fi
    exit 0
fi

REMOTE_SHA=$(git ls-remote "$REPO" refs/heads/main 2>/dev/null | cut -f1 || echo "")

python3 -c "
import json, time
m = json.load(open('$META'))
m['last_update_check'] = time.time()
with open('$META', 'w') as f:
    json.dump(m, f, indent=2)
" 2>/dev/null || true

if [ -z "$REMOTE_SHA" ]; then
    exit 0
fi

if [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
    MSG="Eidetic update available (${REMOTE_SHA:0:7}). Run: bash ~/.claude/memory-system/bin/update.sh"
    echo "$MSG" > "$UPDATE_MARKER"
    echo "$MSG"
else
    rm -f "$UPDATE_MARKER"
fi
