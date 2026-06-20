#!/usr/bin/env python3
"""Resolve the card-extraction (session-end signal) model id — one source of truth
for the Stop hook (session-signals.sh) and the doctor display.

Resolution order (mirrors embed.py's profile resolution):
  1. EIDETIC_SIGNAL_CLAUDE_MODEL — an explicit full id wins (backward-compatible
     runtime override).
  2. <memory-system>/.signal_model — the INSTALL-TIME choice: a friendly name
     (sonnet | haiku) mapped to a pinned id, or a full "claude-..." id verbatim.
  3. default: claude-sonnet-4-6 (quality).

An EXACT id is always pinned, never the bare 'sonnet' alias: a user's
ANTHROPIC_DEFAULT_SONNET_MODEL remap (e.g. sonnet -> Opus) would otherwise silently
run this background extraction on a flagship model and drain the shared quota pool.
"""

import os
import sys

# Friendly install choices -> pinned exact ids. Update the ids here when the model
# generation moves; the install UX and configs keep using the stable friendly names.
NAMES = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}
DEFAULT = "claude-sonnet-4-6"


def _root(env, root):
    return os.path.expanduser(
        root or env.get("EIDETIC_MEMORY_SYSTEM") or "~/.claude/memory-system")


def _file_choice(env, root):
    try:
        with open(os.path.join(_root(env, root), ".signal_model"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def resolve(env=None, root=None):
    """The full model id to run signal extraction with."""
    env = os.environ if env is None else env
    explicit = (env.get("EIDETIC_SIGNAL_CLAUDE_MODEL") or "").strip()
    if explicit:
        return explicit
    choice = _file_choice(env, root)
    if choice in NAMES:
        return NAMES[choice]
    if choice.startswith("claude-"):
        return choice
    return DEFAULT


def describe(env=None, root=None):
    """Human label for the doctor: '<source>: <friendly?> -> <id>'."""
    env = os.environ if env is None else env
    explicit = (env.get("EIDETIC_SIGNAL_CLAUDE_MODEL") or "").strip()
    if explicit:
        return f"env -> {explicit}"
    choice = _file_choice(env, root)
    if choice in NAMES:
        return f"{choice} -> {NAMES[choice]}  (.signal_model)"
    if choice.startswith("claude-"):
        return f"{choice}  (.signal_model)"
    return f"sonnet -> {DEFAULT}  (default)"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--describe" in argv:
        print(describe())
    else:
        print(resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
