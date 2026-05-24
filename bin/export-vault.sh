#!/usr/bin/env bash
# Eidetic export-vault — export AI memory to Obsidian vault, register, and open.
# Plug and play: one command → vault ready in Obsidian.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_VAULT="$HOME/Documents/eidetic-vault"
OBSIDIAN_CONFIG="$HOME/Library/Application Support/obsidian/obsidian.json"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'EOF'
Usage: eidetic export-vault [target-dir] [options]

If no target-dir given, exports to ~/Documents/eidetic-vault/

Options:
  --project <slug>   Export only one project (fuzzy match)
  --delta            Only export changed files since last export
  --no-polish        Skip LLM polish (faster, no API calls)
  --no-synthesize    Skip topic synthesis (faster, no API calls)
  --all --force      Skip quality gate (raw dump)
  --no-open          Don't open Obsidian after export
  -h, --help         Show this help

Examples:
  eidetic export-vault                          # export + LLM polish/synthesis + open
  eidetic export-vault ~/my-vault/              # custom location
  eidetic export-vault --delta                  # incremental update + open
  eidetic export-vault --no-polish --no-synthesize --no-open
  eidetic export-vault --delta --no-open        # update without opening
EOF
    exit 0
fi

# Parse: first non-flag arg is target_dir, rest passed through
TARGET=""
PASS_ARGS=()
NO_OPEN=false
for arg in "$@"; do
    if [ "$arg" = "--no-open" ]; then
        NO_OPEN=true
    elif [ -z "$TARGET" ] && [[ ! "$arg" == --* ]]; then
        TARGET="$arg"
    else
        PASS_ARGS+=("$arg")
    fi
done
[ -z "$TARGET" ] && TARGET="$DEFAULT_VAULT"

# Export
python3 "$SCRIPT_DIR/export_vault.py" "$TARGET" "${PASS_ARGS[@]+"${PASS_ARGS[@]}"}"

# Register vault in Obsidian (macOS only)
if [ "$NO_OPEN" = false ] && [ "$(uname)" = "Darwin" ] && [ -f "$OBSIDIAN_CONFIG" ]; then
    ABS_TARGET="$(cd "$TARGET" && pwd)"
    python3 - "$ABS_TARGET" "$OBSIDIAN_CONFIG" <<'PYEOF'
import json, sys, hashlib, time, os

vault_path = sys.argv[1]
config_path = sys.argv[2]

with open(config_path) as f:
    config = json.load(f)

vaults = config.get("vaults", {})
already = any(v.get("path") == vault_path for v in vaults.values())

if not already:
    vault_id = hashlib.md5(vault_path.encode()).hexdigest()[:16]
    vaults[vault_id] = {"path": vault_path, "ts": int(time.time() * 1000)}
    config["vaults"] = vaults
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print("  Registered vault in Obsidian: {}".format(vault_id))
PYEOF

    # Open in Obsidian
    ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$ABS_TARGET'))")
    open "obsidian://open?path=$ENCODED" 2>/dev/null && echo "  Opened in Obsidian." || true
fi
