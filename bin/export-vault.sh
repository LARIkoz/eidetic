#!/usr/bin/env bash
# Eidetic export-vault — project AI memory into an Obsidian vault.
# Usage: eidetic export-vault <target-dir> [--project <slug>] [--delta] [--all --force]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -eq 0 ] || [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    cat <<'EOF'
Usage: eidetic export-vault <target-dir> [options]

Options:
  --project <slug>   Export only one project (fuzzy match)
  --delta            Only export changed files since last export
  --all --force      Skip quality gate (raw dump, requires --force)
  -h, --help         Show this help

Examples:
  eidetic export-vault ~/my-vault/
  eidetic export-vault ~/my-vault/ --project gap-pipeline
  eidetic export-vault ~/my-vault/ --delta
EOF
    exit 0
fi

exec python3 "$SCRIPT_DIR/export_vault.py" "$@"
