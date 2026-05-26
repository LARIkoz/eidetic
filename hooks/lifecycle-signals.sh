#!/bin/bash
# Eidetic v4.3 — metadata-only lifecycle event capture.
# Synchronous PostToolUse hook with timeout configured in Claude settings.
set -euo pipefail

MEMORY_SYSTEM="${EIDETIC_MEMORY_SYSTEM:-$HOME/.claude/memory-system}"
HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"

SCRIPT="$MEMORY_SYSTEM/bin/lifecycle_signals.py"
if [ ! -f "$SCRIPT" ] && [ -f "$HOOK_DIR/../bin/lifecycle_signals.py" ]; then
    SCRIPT="$HOOK_DIR/../bin/lifecycle_signals.py"
fi

if [ ! -f "$SCRIPT" ]; then
    exit 0
fi

python3 "$SCRIPT" >/dev/null 2>/dev/null || true
exit 0
