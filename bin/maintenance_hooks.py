"""Idempotent registration of bounded maintenance, shared by install/update."""

import os
import shlex


def _owned_command(command, memory_system):
    """Recognize executable positions, never incidental text or foreign paths."""
    try:
        words = shlex.split(command)
    except ValueError:
        return False, []
    overrides = []
    while words:
        word = words[0]
        if word.startswith("EIDETIC_") and "=" in word:
            key, value = words.pop(0).split("=", 1)
            if not key.replace("_", "").isalnum():
                return False, []
            overrides.append(key + "=" + shlex.quote(os.path.expandvars(value)))
        elif word in ("nohup", "/usr/bin/nohup", "env", "/usr/bin/env"):
            words.pop(0)
        else:
            break
    if not words:
        return False, []
    expanded = [os.path.expanduser(os.path.expandvars(w)) for w in words]
    hook = os.path.expanduser("~/.claude/hooks/semantic-maintenance.sh")
    if expanded[0] == hook:
        return True, overrides
    if expanded[0] in ("bash", "/bin/bash") and len(expanded) > 1 and expanded[1] == hook:
        return True, overrides
    roots = {os.path.expanduser("~/.claude/memory-system"),
             os.path.abspath(os.path.expanduser(memory_system)) if memory_system else ""}
    if os.path.basename(expanded[0]) in ("taskpolicy", "nice", "python3", "python"):
        for root in roots - {""}:
            if all(os.path.join(root, item) in expanded for item in
                   ("bin/lock_runner.py", ".m2-background.lock", "bin/index.sh")):
                return True, overrides
    return False, []


def ensure_maintenance_hook(settings, memory_system=""):
    prefix = ""
    default = os.path.expanduser("~/.claude/memory-system")
    if memory_system and os.path.abspath(os.path.expanduser(memory_system)) != default:
        prefix = "EIDETIC_MEMORY_SYSTEM={} ".format(shlex.quote(memory_system))
    command = prefix + '/bin/bash "$HOME/.claude/hooks/semantic-maintenance.sh"'
    replacement = {"type": "command", "command": command, "timeout": 90, "async": True}
    stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
    registered = False
    for entry in stop:
        if not isinstance(entry, dict):
            continue
        kept = []
        for hook in entry.get("hooks", []):
            old = str(hook.get("command", "")) if isinstance(hook, dict) else ""
            # Recognize only our named hook or the historical single-flight
            # launcher. Other index commands and user Stop hooks are not ours.
            owned, overrides = _owned_command(old, memory_system)
            if not owned:
                kept.append(hook)
            elif not registered:
                migrated = dict(replacement)
                if prefix:
                    overrides = [v for v in overrides if not v.startswith("EIDETIC_MEMORY_SYSTEM=")]
                migrated["command"] = " ".join(overrides + [command])
                kept.append(migrated)
                registered = True
        if "hooks" in entry:
            entry["hooks"] = kept
    if not registered:
        stop.append({"hooks": [replacement]})
