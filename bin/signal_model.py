#!/usr/bin/env python3
"""Resolve the card-extraction (session-end signal) model id — one source of truth
for the Stop hook (session-signals.sh) and the doctor display.

Resolution order (mirrors embed.py's profile resolution):
  1. EIDETIC_SIGNAL_CLAUDE_MODEL — runtime override: a friendly name (sonnet |
     haiku) mapped to a pinned id, or a full "claude-..." id verbatim.
  2. <memory-system>/.signal_model — the INSTALL-TIME choice, same normalization.
  3. default: claude-sonnet-4-6 (quality).

Both the env override and the file normalize identically (see _normalize): the env
path previously returned its value verbatim, so the documented
EIDETIC_SIGNAL_CLAUDE_MODEL=haiku leaked the bare alias 'haiku' into ANTHROPIC_MODEL
on the live Stop hook instead of the pinned claude-haiku id.

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


def _normalize(choice):
    """A config value -> a pinned exact id, or None if unrecognized.

    Friendly name (sonnet|haiku, case-insensitive) -> pinned id; a full "claude-..."
    id passes through verbatim; anything else (empty, typo, bare alias like a raw
    'sonnet'-remap target) -> None so the caller falls through to the next source.
    The env override and the .signal_model file MUST normalize the same way — the
    env path used to return its value verbatim, so the documented
    EIDETIC_SIGNAL_CLAUDE_MODEL=haiku leaked the bare alias 'haiku' to ANTHROPIC_MODEL.
    """
    if not choice:
        return None
    if choice.lower() in NAMES:
        return NAMES[choice.lower()]
    if choice.startswith("claude-"):
        return choice
    return None


def _label(choice):
    return choice.lower() if choice.lower() in NAMES else choice


def resolve(env=None, root=None):
    """The full model id to run signal extraction with."""
    env = os.environ if env is None else env
    explicit = (env.get("EIDETIC_SIGNAL_CLAUDE_MODEL") or "").strip()
    return _normalize(explicit) or _normalize(_file_choice(env, root)) or DEFAULT


def describe(env=None, root=None):
    """Human label for the doctor: '<source>: <friendly?> -> <id>'."""
    env = os.environ if env is None else env
    explicit = (env.get("EIDETIC_SIGNAL_CLAUDE_MODEL") or "").strip()
    if _normalize(explicit):
        return f"env {_label(explicit)} -> {_normalize(explicit)}"
    choice = _file_choice(env, root)
    if _normalize(choice):
        return f"{_label(choice)} -> {_normalize(choice)}  (.signal_model)"
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
