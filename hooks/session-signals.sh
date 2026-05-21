#!/bin/bash
# AI Memory System v1 — Session Signal Extraction (Stop hook, async)
# Extracts decisions/rules/failures from session transcript via Haiku.
# Runs async — does not delay session end.
# Source: agent-extracted (0.5x self-referential discount)
set -euo pipefail

MEMORY_SYSTEM="$HOME/.claude/memory-system"
LOCKFILE="$MEMORY_SYSTEM/.memory.lock"
COMPOUND="$MEMORY_SYSTEM/bin/compound.py"
INDEX="$MEMORY_SYSTEM/bin/index.sh"

# Read transcript path from stdin JSON
INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || echo "")

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    exit 0
fi

# Skip short sessions (< 1000 chars = nothing useful)
TSIZE=$(wc -c < "$TRANSCRIPT" 2>/dev/null || echo 0)
if [ "$TSIZE" -lt 1000 ]; then
    exit 0
fi

# Extract last ~4000 chars of transcript for signal extraction (cost control)
EXCERPT=$(tail -c 8000 "$TRANSCRIPT" | python3 -c "
import sys, json
lines = []
for line in sys.stdin:
    try:
        msg = json.loads(line.strip())
        role = msg.get('role', '')
        content = msg.get('content', '')
        if isinstance(content, list):
            content = ' '.join(c.get('text','') for c in content if isinstance(c,dict))
        if role in ('user','assistant') and content:
            lines.append(f'{role}: {content[:500]}')
    except: pass
print('\n'.join(lines[-20:]))
" 2>/dev/null || tail -c 4000 "$TRANSCRIPT")

if [ -z "$EXCERPT" ]; then
    exit 0
fi

PROMPT="Extract useful signals from this session transcript. For each signal, write ONE line (1-3 sentences). Focus on:
- Decisions made and rationale
- Rules invoked or violated
- What worked well
- What failed and why
- New technical knowledge learned

Output format (plain text, one signal per line, no bullets or numbers):
Decision: ...
Rule: ...
Worked: ...
Failed: ...
Knowledge: ...

Only include signals that would be useful in future sessions. Skip generic observations. If nothing notable happened, output EMPTY.

Transcript:
$EXCERPT"

# Run via claude-batch (async, haiku for cost)
# Use claude --print with haiku model for signal extraction
# Falls back to EMPTY if claude CLI not available
RESULT=$(claude -p "$PROMPT" --model haiku 2>/dev/null || echo "EMPTY")

if [ -z "$RESULT" ] || echo "$RESULT" | grep -qi "^EMPTY$"; then
    exit 0
fi

# Acquire lock for compound + reindex (shared with SessionStart hook)
LOCKDIR="$MEMORY_SYSTEM/.memory.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f%m "$LOCKDIR" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 30 ]; then rm -r "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null || true; fi
    if [ ! -d "$LOCKDIR" ]; then exit 0; fi
fi
trap 'rm -r "$LOCKDIR" 2>/dev/null' EXIT

# Pass to compounding logic
echo "$RESULT" | python3 "$COMPOUND" "$(pwd)" 2>/dev/null || exit 0

# Reindex to make new signals searchable
"$INDEX" --incremental >/dev/null 2>&1 || true

exit 0
