#!/usr/bin/env python3
"""Metadata-only Claude Code lifecycle event capture for Eidetic.

This module intentionally stores only bounded derived metadata. It never reads
or persists file contents, diffs, stdout/stderr, tool results, transcript text,
raw cwd, raw paths, raw relative paths, or raw filenames.
"""

from __future__ import annotations

import argparse
import hmac
import hashlib
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 512
EVENT_DIR = Path("events") / "lifecycle"
KEY_NAME = ".hmac_key"
ALLOWED_TOOLS = {"Write", "Edit", "MultiEdit"}
OPERATION_BY_TOOL = {
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "multi_edit",
}
SENSITIVE_NAMES = {
    ".env",
    "keys.env",
    ".npmrc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".db",
}
SENSITIVE_PARTS = {
    ".git",
    ".ssh",
    ".aws",
    "shared_api_cache",
    ".obsidian",
    "eidetic-vault",
}


def default_memory_system() -> Path:
    installed_root = Path(__file__).resolve().parent.parent
    if (installed_root / ".installed.json").exists():
        return installed_root
    return Path.home() / ".claude" / "memory-system"


def memory_system_from_env() -> Path:
    return Path(os.environ.get("EIDETIC_MEMORY_SYSTEM") or default_memory_system()).expanduser()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_parent_ref(path_text: str) -> bool:
    return any(part == ".." for part in Path(path_text).parts)


def _resolve_path(path: Path) -> Optional[Path]:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError:
        return None


def _path_parts(path: Path) -> Iterable[str]:
    for part in path.parts:
        yield part.lower()


def _looks_sensitive(path: Path) -> bool:
    parts = list(_path_parts(path))
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in SENSITIVE_NAMES:
        return True
    if name.startswith(".env."):
        return True
    if suffix in SENSITIVE_SUFFIXES:
        return True
    if any(part in SENSITIVE_PARTS for part in parts):
        return True
    if ".aws" in parts and "credentials" in parts:
        return True
    return False


def _configured_vault_roots() -> Iterable[Path]:
    yield Path.home() / "Documents" / "eidetic-vault"
    yield Path.home() / "Documents" / "cursore" / "eidetic-vault"
    for raw in os.environ.get("EIDETIC_VAULT_ROOTS", "").split(":"):
        raw = raw.strip()
        if raw:
            yield Path(raw).expanduser()


def _is_under_configured_vault(path: Path) -> bool:
    for root in _configured_vault_roots():
        resolved = _resolve_path(root)
        if resolved and _is_relative_to(path, resolved):
            return True
    return False


def _manifest_marks_eidetic(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            sample = f.read(4096)
    except OSError:
        return False
    return '"_exported_by"' in sample and '"eidetic"' in sample


def _is_in_vault_projection(path: Path) -> bool:
    if _is_under_configured_vault(path):
        return True
    parts = set(_path_parts(path))
    if ".obsidian" in parts or "eidetic-vault" in parts:
        return True

    home = _resolve_path(Path.home())
    current = path if path.is_dir() else path.parent
    seen = 0
    while True:
        if (current / ".obsidian").is_dir():
            return True
        manifest = current / ".manifest.json"
        if manifest.is_file() and _manifest_marks_eidetic(manifest):
            return True
        if current == current.parent:
            return False
        if home and current == home:
            return False
        current = current.parent
        seen += 1
        if seen > 256:
            return False


def _target_ext(path: Path) -> str:
    if path.name.startswith(".") and path.name.count(".") == 1:
        return ""
    return path.suffix.lower()


def _duration_ms(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _edit_count(tool_name: str, tool_input: Dict[str, Any]) -> int:
    if tool_name != "MultiEdit":
        return 1
    edits = tool_input.get("edits")
    if isinstance(edits, list) and edits:
        return len(edits)
    return 1


def _event_path(memory_system: Path, now: Optional[datetime] = None) -> Path:
    now = now or datetime.now(timezone.utc)
    return memory_system / EVENT_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"


def _key_path(memory_system: Path) -> Path:
    return memory_system / EVENT_DIR / KEY_NAME


def _load_or_create_key(memory_system: Path) -> Optional[bytes]:
    key_path = _key_path(memory_system)
    try:
        key_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(key_path.parent, 0o700)
    except OSError:
        return None

    try:
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    except OSError:
        return None
    else:
        try:
            key = secrets.token_hex(32).encode("ascii")
            os.write(fd, key)
        except OSError:
            return None
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            return None

    try:
        mode = stat.S_IMODE(key_path.stat().st_mode)
        if mode != 0o600:
            os.chmod(key_path, 0o600)
        key = key_path.read_bytes().strip()
    except OSError:
        return None
    if len(key) < 32:
        return None
    return key


def _hmac_hex(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8", "surrogateescape"), hashlib.sha256).hexdigest()


def _recorded_at(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _compact_json(record: Dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _event_limit() -> int:
    raw = os.environ.get("EIDETIC_LIFECYCLE_MAX_EVENT_BYTES")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return MAX_EVENT_BYTES


def _bounded_json(record: Dict[str, Any], limit: Optional[int] = None) -> Optional[bytes]:
    record = dict(record)
    limit = limit or _event_limit()
    data = _compact_json(record)
    if len(data) <= limit:
        return data
    for key in ("duration_ms", "tool_use_id", "session_id", "cwd_hash"):
        record.pop(key, None)
        data = _compact_json(record)
        if len(data) <= limit:
            return data
    return None


def _atomic_append_jsonl(path: Path, data: bytes) -> bool:
    try:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = os.write(fd, data)
        finally:
            os.close(fd)
        if written != len(data):
            return False
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return True
    except OSError:
        return False


def build_record(payload: Dict[str, Any], memory_system: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    tool_name = payload.get("tool_name")
    if tool_name not in ALLOWED_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw_file_path = tool_input.get("file_path")
    if not isinstance(raw_file_path, str) or not raw_file_path:
        return None
    if _has_parent_ref(raw_file_path):
        return None

    raw_cwd = payload.get("cwd")
    cwd = Path(raw_cwd).expanduser() if isinstance(raw_cwd, str) and raw_cwd else Path.cwd()
    cwd_resolved = _resolve_path(cwd)
    if not cwd_resolved:
        return None

    target = Path(raw_file_path).expanduser()
    if not target.is_absolute():
        target = cwd_resolved / target
    target_resolved = _resolve_path(target)
    if not target_resolved:
        return None

    memory_system = memory_system or memory_system_from_env()
    memory_resolved = _resolve_path(memory_system)
    if not memory_resolved:
        return None

    if not (_is_relative_to(target_resolved, cwd_resolved) or _is_relative_to(target_resolved, memory_resolved)):
        return None
    if _is_relative_to(target_resolved, memory_resolved / "db"):
        return None
    if _looks_sensitive(target_resolved):
        return None
    if _is_in_vault_projection(target_resolved):
        return None

    key = _load_or_create_key(memory_resolved)
    if not key:
        return None

    cwd_text = str(cwd_resolved)
    path_text = str(target_resolved)
    cwd_hash = _hmac_hex(key, cwd_text)
    path_hash = _hmac_hex(key, path_text)
    record = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": _recorded_at(),
        "hook_event_name": "PostToolUse",
        "session_id": str(payload.get("session_id") or ""),
        "tool_name": tool_name,
        "tool_use_id": str(payload.get("tool_use_id") or ""),
        "duration_ms": _duration_ms(payload.get("duration_ms")),
        "project_slug": f"project_{cwd_hash[:8]}",
        "cwd_hash": f"hmac_sha256:{cwd_hash}",
        "path_hash": f"hmac_sha256:{path_hash}",
        "target_ext": _target_ext(target_resolved),
        "operation": OPERATION_BY_TOOL[tool_name],
        "edit_count": _edit_count(tool_name, tool_input),
    }
    return record


def write_event(payload: Dict[str, Any], memory_system: Optional[Path] = None) -> bool:
    memory_system = memory_system or memory_system_from_env()
    memory_resolved = _resolve_path(memory_system)
    if not memory_resolved:
        return False
    record = build_record(payload, memory_resolved)
    if not record:
        return False
    data = _bounded_json(record)
    if not data:
        return False
    return _atomic_append_jsonl(_event_path(memory_resolved), data)


def _hook_prefix(memory_system: str) -> str:
    default = str(Path.home() / ".claude" / "memory-system")
    if memory_system and os.path.abspath(os.path.expanduser(memory_system)) != os.path.abspath(default):
        import shlex

        return "EIDETIC_MEMORY_SYSTEM={} ".format(shlex.quote(memory_system))
    return ""


def ensure_lifecycle_hook(settings: Dict[str, Any], memory_system: str = "") -> bool:
    """Add/update the dedicated PostToolUse lifecycle hook entry.

    Returns True if an existing lifecycle hook was replaced, False if it was
    newly appended. Other PostToolUse entries are preserved.
    """

    hooks = settings.setdefault("hooks", {})
    post_tool = hooks.setdefault("PostToolUse", [])
    if not isinstance(post_tool, list):
        hooks["PostToolUse"] = post_tool = []

    found = False
    kept_entries = []
    for entry in post_tool:
        if not isinstance(entry, dict):
            kept_entries.append(entry)
            continue
        old_hooks = entry.get("hooks", [])
        if not isinstance(old_hooks, list):
            kept_entries.append(entry)
            continue
        new_hooks = []
        for hook in old_hooks:
            command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
            if "lifecycle-signals" in command:
                found = True
            else:
                new_hooks.append(hook)
        if new_hooks:
            copied = dict(entry)
            copied["hooks"] = new_hooks
            kept_entries.append(copied)

    lifecycle_hook = {
        "type": "command",
        "command": _hook_prefix(memory_system) + "~/.claude/hooks/lifecycle-signals.sh",
        # Claude Code hook timeout is in seconds in the current hooks docs.
        "timeout": 2,
    }
    kept_entries.append({
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [lifecycle_hook],
    })
    hooks["PostToolUse"] = kept_entries
    return found


def _load_stdin_payload() -> Optional[Dict[str, Any]]:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-settings", help="Validate/register lifecycle hook in a settings JSON file")
    args = parser.parse_args(argv)

    if args.check_settings:
        try:
            with open(args.check_settings, encoding="utf-8") as f:
                settings = json.load(f)
            ensure_lifecycle_hook(settings, str(memory_system_from_env()))
        except Exception:
            return 1
        return 0

    payload = _load_stdin_payload()
    if payload:
        write_event(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
