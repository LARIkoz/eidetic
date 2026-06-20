#!/bin/bash
# Eidetic — Installer (with auto-update support)
# Zero external deps: bash + python3 + sqlite3 (all pre-installed on macOS/Linux)
set -euo pipefail

MEMORY_SYSTEM="${EIDETIC_MEMORY_SYSTEM:-$HOME/.claude/memory-system}"
HOOKS_DIR="$HOME/.claude/hooks"
SKILLS_DIR="$HOME/.claude/skills/memory-recall"
RULES_DIR="$HOME/.claude/rules"
SETTINGS="$HOME/.claude/settings.json"
META="$MEMORY_SYSTEM/.installed.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_URL="https://github.com/LARIkoz/eidetic.git"

atomic_install() {
    src="$1"
    dst="$2"
    mode="${3:-}"
    mkdir -p "$(dirname "$dst")"
    tmp=$(mktemp "${dst}.tmp.XXXXXX")
    if ! cp "$src" "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if [ -n "$mode" ]; then
        chmod "$mode" "$tmp"
    fi
    if ! mv -f "$tmp" "$dst"; then
        rm -f "$tmp"
        return 1
    fi
}

echo "=== Eidetic Memory System — Install ==="
echo ""

# Check prerequisites
command -v python3 >/dev/null || { echo "ERROR: python3 required"; exit 1; }
command -v sqlite3 >/dev/null || { echo "ERROR: sqlite3 required"; exit 1; }
[ -d "$HOME/.claude" ] || { echo "ERROR: ~/.claude/ not found. Install Claude Code first."; exit 1; }

# Backup current state
echo "1. Creating backup..."
if [ -f "$SETTINGS" ]; then
    cp "$SETTINGS" "$SETTINGS.pre-memory-system"
    echo "   Backed up settings.json"
fi
for f in "$HOOKS_DIR"/*.sh; do
    [ -f "$f" ] && cp "$f" "$f.bak"
done
echo "   Backed up existing hooks"

# Install memory system
echo "2. Installing memory system..."
mkdir -p "$MEMORY_SYSTEM"/{bin,db}
for src in bin/*.sh; do
    atomic_install "$src" "$MEMORY_SYSTEM/bin/$(basename "$src")" 755
done
for src in bin/*.py; do
    mode=644
    [ -x "$src" ] && mode=755
    atomic_install "$src" "$MEMORY_SYSTEM/bin/$(basename "$src")" "$mode"
done
atomic_install mcp_server.py "$MEMORY_SYSTEM/mcp_server.py" 644

# Embedding profile (model-by-language): multilingual (default) or english.
#   multilingual = multilingual-e5-large (1024d, ~100 langs, ~2.2GB cache)
#   english      = bge-small-en-v1.5 (384d, ~130MB cache, ~5x faster embed) —
#                  equal English recall@3 in A/B, for English-only corpora.
# Opt in:  EIDETIC_EMBED_PROFILE=english bash install.sh
# Switch later: write the name to "$MEMORY_SYSTEM/.embed_profile", then
# `bash bin/index.sh --full` (the model/dim stamp guard forces a clean rebuild).
EMBED_PROFILE="${EIDETIC_EMBED_PROFILE:-multilingual}"
case "$EMBED_PROFILE" in multilingual|english) ;; *) EMBED_PROFILE=multilingual ;; esac
printf '%s\n' "$EMBED_PROFILE" > "$MEMORY_SYSTEM/.embed_profile"
echo "   Embedding profile: $EMBED_PROFILE"

# Query translation (cross-lingual, opt-in): off (default) | auto | apple | opusmt | cli.
#   A non-English query is translated to English and dual-queried (native + translated,
#   min-rank fused) — adds recall, never regresses (5/8 -> 7/8 @3). All backends fail-open.
#   apple  = Apple Translation NMT (macOS 26+, on-device; install the pair once via
#            System Settings -> Translation Languages).
#   opusmt = Opus-MT / CTranslate2 (portable; pip ctranslate2 sentencepiece huggingface_hub + ~75MB model).
# Opt in:  EIDETIC_QUERY_TRANSLATE=auto bash install.sh   (or write the name to .translate_backend later)
TRANSLATE_BACKEND="${EIDETIC_QUERY_TRANSLATE:-off}"
case "$TRANSLATE_BACKEND" in off|auto|apple|opusmt|cli) ;; *) TRANSLATE_BACKEND=off ;; esac
printf '%s\n' "$TRANSLATE_BACKEND" > "$MEMORY_SYSTEM/.translate_backend"
echo "   Query translation: $TRANSLATE_BACKEND"

# Install hooks
echo "3. Installing hooks..."
mkdir -p "$HOOKS_DIR"
for src in hooks/*.sh; do
    atomic_install "$src" "$HOOKS_DIR/$(basename "$src")" 755
done

# Install skill
echo "4. Installing recall skill..."
mkdir -p "$SKILLS_DIR"
atomic_install skill/SKILL.md "$SKILLS_DIR/SKILL.md" 644
if [ "$MEMORY_SYSTEM" != "$HOME/.claude/memory-system" ]; then
    python3 - "$SKILLS_DIR/SKILL.md" "$MEMORY_SYSTEM" << 'PYEOF'
import os, pathlib, shlex, sys, tempfile

path = pathlib.Path(sys.argv[1])
memory_system = sys.argv[2]
text = path.read_text(encoding="utf-8")
fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(text.replace("~/.claude/memory-system", shlex.quote(memory_system)))
os.replace(tmp, path)
PYEOF
fi

# Create rules directory
mkdir -p "$RULES_DIR"

# Register hooks in settings.json
echo "5. Registering hooks..."
if [ -f "$SETTINGS" ]; then
    EIDETIC_INSTALL_MEMORY_SYSTEM="$MEMORY_SYSTEM" python3 << 'PYEOF'
import json, os, shlex, sys, tempfile

settings_path = os.path.expanduser("~/.claude/settings.json")
with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})
memory_system = os.environ.get("EIDETIC_INSTALL_MEMORY_SYSTEM", "")
default_memory_system = os.path.expanduser("~/.claude/memory-system")
sys.path.insert(0, os.path.join(memory_system or default_memory_system, "bin"))
from lifecycle_signals import ensure_lifecycle_hook

hook_prefix = ""
if memory_system and os.path.abspath(os.path.expanduser(memory_system)) != os.path.abspath(default_memory_system):
    hook_prefix = "EIDETIC_MEMORY_SYSTEM={} ".format(shlex.quote(memory_system))

# Add SessionStart hook (after existing ones)
session_start = hooks.setdefault("SessionStart", [])
inject_hook = {
    "hooks": [{
        "type": "command",
        "command": hook_prefix + "~/.claude/hooks/smart-memory-inject.sh",
        "timeout": 5000
    }]
}
inject_updated = False
for entry in session_start:
    for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
        if "smart-memory-inject" in str(hook.get("command", "")):
            hook.update(inject_hook["hooks"][0])
            inject_updated = True
if inject_updated:
    print("   Updated smart-memory-inject in SessionStart")
elif not any("smart-memory-inject" in str(h) for h in session_start):
    session_start.append(inject_hook)
    print("   Added smart-memory-inject to SessionStart")
else:
    print("   smart-memory-inject already registered")

# Add Stop hook (async)
stop = hooks.setdefault("Stop", [])
signal_entry = {
    "type": "command",
    "command": hook_prefix + "~/.claude/hooks/session-signals.sh",
    "timeout": 180000,
    "async": True
}
signal_updated = False
for entry in stop:
    for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
        if "session-signals" in str(hook.get("command", "")):
            hook.update(signal_entry)
            signal_updated = True
if signal_updated:
    print("   Updated session-signals in Stop")
elif not any("session-signals" in str(h) for h in stop):
    if stop and "hooks" in stop[0]:
        stop[0]["hooks"].append(signal_entry)
    else:
        stop.append({"hooks": [signal_entry]})
    print("   Added session-signals to Stop")
else:
    print("   session-signals already registered")

lifecycle_updated = ensure_lifecycle_hook(settings, memory_system)
if lifecycle_updated:
    print("   Updated lifecycle-signals in PostToolUse")
else:
    print("   Added lifecycle-signals to PostToolUse")

settings_dir = os.path.dirname(settings_path) or "."
fd, tmp = tempfile.mkstemp(dir=settings_dir, prefix=os.path.basename(settings_path) + ".tmp.")
with os.fdopen(fd, "w") as f:
    json.dump(settings, f, indent=2)
os.replace(tmp, settings_path)
PYEOF
fi

# Write install metadata (for auto-updates)
echo "6. Writing install metadata..."
GIT_SHA=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
VERSION=$(sed -n 's/.*version-\([0-9][0-9.]*\)-.*/\1/p' "$SCRIPT_DIR/README.md" 2>/dev/null | head -1)
[ -z "$VERSION" ] && VERSION="unknown"
python3 - "$META" "$VERSION" "$GIT_SHA" "$REPO_URL" << 'PYEOF'
import json, os, sys, tempfile, time

meta_path, version, git_sha, repo_url = sys.argv[1:5]
meta_path = os.path.expanduser(meta_path)
meta = {
    "version": version,
    "git_sha": git_sha,
    "repo": repo_url,
    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "update_method": "install"
}
os.makedirs(os.path.dirname(meta_path), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(meta_path), prefix=os.path.basename(meta_path) + ".tmp.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2)
os.replace(tmp, meta_path)
print(f"   Version: {meta['version']} ({meta['git_sha'][:7]})")
PYEOF

# Build initial index
echo "7. Building FTS5 index..."
"$MEMORY_SYSTEM/bin/index.sh" --full 2>&1

# Run health check
echo ""
echo "8. Health check..."
"$MEMORY_SYSTEM/bin/health.sh"

# Optional daily vault export cron.
# Keep install non-interactive; opt in with EIDETIC_SETUP_CRON=1.
echo ""
echo "9. Optional daily vault export (cron)..."
if [ "${EIDETIC_SETUP_CRON:-0}" = "1" ]; then
    VAULT_DIR="${VAULT_DIR:-$HOME/Documents/eidetic-vault}"
    CRON_EXPORT_CMD=$(printf 'bash %q %q --delta --no-polish --no-synthesize --no-open' "$MEMORY_SYSTEM/bin/export-vault.sh" "$VAULT_DIR")
    (
        crontab -l 2>/dev/null | grep -Ev "memory-system/bin/export-vault\\.sh|eidetic-vault-cron\\.log" || true
        echo "0 3 * * * $CRON_EXPORT_CMD >> /tmp/eidetic-vault-cron.log 2>&1"
    ) | crontab -
    echo "   Cron job added: daily at 3am -> $VAULT_DIR"
else
    echo "   Skipped. Enable with: EIDETIC_SETUP_CRON=1 bash install.sh"
fi

echo ""
echo "=== Installation complete ==="
echo ""
COMMAND_MEMORY_SYSTEM=$(printf '%q' "$MEMORY_SYSTEM")
echo "What happens now:"
echo "  - Every session start: rules auto-injected + update check (background)"
echo "  - Every session end: signals extracted and compounded"
echo "  - Search: $COMMAND_MEMORY_SYSTEM/bin/search.sh \"your query\""
echo "  - Recall skill: /memory-recall in Claude Code"
echo "  - Health: $COMMAND_MEMORY_SYSTEM/bin/health.sh"
echo "  - Update: $COMMAND_MEMORY_SYSTEM/bin/update.sh"
echo "  - Rollback: bash $COMMAND_MEMORY_SYSTEM/bin/rollback.sh"
echo ""
echo "Auto-updates:"
echo "  - Checks for updates every 6 hours (at session start, background)"
echo "  - Shows a one-line notice when an update is available"
echo "  - Run 'bash $COMMAND_MEMORY_SYSTEM/bin/update.sh' to apply"
echo ""
echo "Obsidian vault export (v4.2):"
echo "  - Export: bash $COMMAND_MEMORY_SYSTEM/bin/export-vault.sh ~/my-vault/"
echo "  - Delta:  bash $COMMAND_MEMORY_SYSTEM/bin/export-vault.sh ~/my-vault/ --delta"
echo ""
echo "Optional v2 features:"
echo "  - Semantic vector search: python3 -m pip install --user fastembed"
echo "  - English-only corpus? Smaller/faster embedder: re-run with EIDETIC_EMBED_PROFILE=english, then bin/index.sh --full"
echo "  - Cross-lingual search? Translate non-English queries: re-run with EIDETIC_QUERY_TRANSLATE=auto (Apple NMT on macOS 26, else Opus-MT/CTranslate2)"
echo "  - Code-aware indexing: python3 -m pip install --user tree-sitter tree-sitter-python tree-sitter-javascript tree-sitter-bash"
echo "  - Without these packages, core FTS5 search still works."
