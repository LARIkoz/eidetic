#!/usr/bin/env python3
"""Fail-closed replay and salvage of pre-policy M3 acquisition evidence.

The tool has no production write mode.  It inventories append-only legacy
evidence, re-mines the original transcript under the current policy, and writes
only to an explicitly supplied replay directory outside the live memory-system
and transcript roots.  A survivor is a review candidate, never a filed card.

Commands:

  inventory  Freeze hashes, row identities, policy/outcome distributions, and
             transcript resolvability without copying claims or transcript text.
  replay     Run a bounded, resumable replay. Judge calls require --enable-judge.
  review     Render a local human-review packet from replay-only artifacts.
"""
import argparse
import contextlib
import datetime as _dt
import fcntl
import glob
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
from collections import Counter, defaultdict

_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_judge as _judge  # noqa: E402
import m3_recall_miner as _miner  # noqa: E402

INVENTORY_SCHEMA = "m3-legacy-inventory-v2"
RESULT_SCHEMA = "m3-legacy-replay-record-v2"
CHECKPOINT_SCHEMA = "m3-legacy-replay-checkpoint-v2"
BINDING_SCHEMA = "m3-legacy-manifest-binding-v1"
MINER_CACHE_SCHEMA = "m3-legacy-miner-cache-v2"
REPLAY_POLICY = "m3-legacy-replay-v3"
TERMINAL_OUTCOMES = frozenset({
    "rejected_noise", "salvaged_current_policy", "source_missing",
    "conflict_manual_review", "invalid_evidence",
})
ALL_OUTCOMES = TERMINAL_OUTCOMES | {"transient_retry"}
LIVE_EVENT_NAMES = frozenset({
    "m3_acquisition_dark.jsonl", "m3_agent_dark.jsonl", "m3_judged.jsonl",
    "m3_agent_files.jsonl", "m3_driver.log", "m3_filed.jsonl", "m3_judge.log",
})
MAX_WORKERS = 1  # no adaptive route/key-lane worker is admitted for this replay
SIMILARITY_FLOOR = 0.55
SIMILARITY_MARGIN = 0.15
CONFLICT_FLOOR = 0.72
_NEGATIONS = frozenset({"not", "no", "never", "without", "не", "нет", "никогда", "без"})
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|rk|pk|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]{8,}", re.I),
    re.compile(r"\bauthorization\s*:\s*(?:bearer|basic)\s+[^\s,;]{8,}", re.I),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}\b"),
)
_JUDGE_CALL_LOCK = threading.Lock()


class ReplayError(RuntimeError):
    """A fail-closed replay contract violation."""


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _source_hash(row):
    return _sha_bytes((_canonical(row) + "\n").encode("utf-8"))


def _reject_symlink(path):
    if os.path.lexists(path) and os.path.islink(path):
        raise ReplayError("replay artifact must not be a symlink: " + str(path))


def _atomic_json(path, obj, mode=0o600):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    _reject_symlink(path)
    fd, tmp = tempfile.mkstemp(prefix=".m3-legacy-", dir=parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            json.dump(obj, fh, ensure_ascii=False, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _append_bytes(path, payload):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    _reject_symlink(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ReplayError("cannot securely open replay artifact: %s" %
                          type(exc).__name__) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReplayError("replay append target is not a regular file")
        data = payload.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise ReplayError("short append to replay artifact")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_record(path, record):
    _append_bytes(path, _canonical(record) + "\n")


def _read_jsonl(path):
    rows, malformed = [], []
    try:
        with open(path, encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    malformed.append(number)
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                else:
                    malformed.append(number)
    except FileNotFoundError:
        pass
    return rows, malformed


def _prepare_append(path, manifest_hash):
    """Make a torn final append resumable without deleting or truncating bytes."""
    _reject_symlink(path)
    if not os.path.lexists(path) or os.path.getsize(path) == 0:
        return False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        complete = fh.read(1) == b"\n"
    if complete:
        return False
    _append_bytes(path, "\n")
    _append_record(path, {
        "schema": RESULT_SCHEMA,
        "record_type": "recovery_marker",
        "manifest_sha256": manifest_hash,
        "replay_timestamp": _now_iso(),
        "reason": "truncated_final_append_preserved",
    })
    return True


def _inside(path, root):
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == \
            os.path.realpath(root)
    except ValueError:
        return False


def _protected_roots(memory_system, projects_root, extra_roots=()):
    roots = [memory_system, projects_root,
             os.path.expanduser("~/.claude/agent-memory")]
    roots.extend(extra_roots or ())
    return tuple(sorted(set(os.path.realpath(root) for root in roots if root)))


def _guard_output(path, memory_system, projects_root, extra_roots=()):
    _reject_symlink(path)
    real = os.path.realpath(path)
    for protected in _protected_roots(memory_system, projects_root, extra_roots):
        if protected and _inside(real, protected):
            raise ReplayError("replay output is inside a protected live path")
    if os.path.basename(real) in LIVE_EVENT_NAMES:
        raise ReplayError("replay output uses a live M3 filename")


def _same_path(left, right):
    if os.path.realpath(left) == os.path.realpath(right):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _guard_distinct_output(output, protected_paths=(), protected_dirs=()):
    for protected in protected_paths:
        if protected and _same_path(output, protected):
            raise ReplayError("output path aliases an input or replay state artifact")
    for protected in protected_dirs:
        if protected and _inside(output, protected):
            raise ReplayError("output path is inside protected replay state")


def _git_head(repo_root):
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return None


def _policy_identity():
    files = {}
    for name in ("m3_legacy_replay.py", "m3_recall_miner.py",
                 "m3_acquisition.py", "m3_judge.py"):
        path = os.path.join(_BIN, name)
        files[name] = _sha_file(path)
    return {
        "replay_policy": REPLAY_POLICY,
        "miner_policy": _miner.MINER_POLICY_VERSION,
        "files": files,
    }


def _verify_runtime_policy(memory_system):
    """Abort when the active runtime policy owner differs from repository code."""
    runtime = {}
    mismatches = []
    for name in ("m3_recall_miner.py", "m3_acquisition.py", "m3_judge.py"):
        source_path = os.path.join(_BIN, name)
        runtime_path = os.path.join(memory_system, "bin", name)
        if not os.path.isfile(runtime_path):
            mismatches.append(name + ":missing")
            continue
        source_hash = _sha_file(source_path)
        runtime_hash = _sha_file(runtime_path)
        runtime[name] = runtime_hash
        if source_hash != runtime_hash:
            mismatches.append(name + ":different")
    if mismatches:
        raise ReplayError("installed runtime policy differs: " + ",".join(mismatches))
    return runtime


def _distribution(rows, field, missing="<missing>"):
    values = []
    for row in rows:
        value = row.get(field, missing)
        if value is None:
            value = "<null>"
        elif isinstance(value, bool):
            value = str(value).lower()
        else:
            value = str(value)
        values.append(value)
    return dict(sorted(Counter(values).items()))


def _legacy_outcome(row):
    if row.get("would_file") is True:
        return "would_file"
    if row.get("safety_reject"):
        return "safety_reject"
    if row.get("quote_ok") is False and row.get("judge") is None:
        return "quote_reject"
    if row.get("judge") == "not_entailed":
        return "not_entailed"
    if row.get("judge") in ("judge_unavailable", "error"):
        return str(row.get("judge"))
    return "unresolved"


def _resolve_transcripts(row, lane, projects_root):
    candidates = []
    sid = str(row.get("session_id") or "").strip()
    slug = str(row.get("project_slug") or "").strip()
    agent_file = str(row.get("agent_file") or "").strip()
    if lane == "agent" and agent_file:
        normalized = os.path.normpath(agent_file)
        if os.path.isabs(agent_file) or normalized == ".." or \
                normalized.startswith(".." + os.sep):
            return []
        for base in ([os.path.join(projects_root, slug)] if slug else []) + \
                list(glob.glob(os.path.join(projects_root, "*"))):
            base = os.path.realpath(base)
            candidate = os.path.realpath(os.path.join(base, normalized))
            if _inside(base, projects_root) and _inside(candidate, base) and \
                    os.path.isfile(candidate):
                candidates.append(candidate)
    if sid and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", sid):
        candidates.extend(glob.glob(os.path.join(projects_root, "*", sid + ".jsonl")))
    unique = sorted(set(os.path.realpath(p) for p in candidates
                        if os.path.isfile(p) and _inside(p, projects_root)))
    return unique


def _artifact_meta(path):
    if not os.path.isfile(path):
        return {"path": os.path.realpath(path), "exists": False}
    rows, malformed = _read_jsonl(path)
    st = os.stat(path)
    return {
        "path": os.path.realpath(path), "exists": True,
        "sha256": _sha_file(path), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
        "row_count": len(rows), "malformed_count": len(malformed),
        "policy_distribution": _distribution(rows, "miner_policy"),
    }


def _replay_row_id(source_artifact, row_number, row_hash, transcript_sha256,
                   current_policy):
    return _sha_bytes(_canonical({
        "replay_policy": REPLAY_POLICY,
        "source_artifact": os.path.realpath(source_artifact),
        "source_row_number": row_number,
        "source_row_hash": row_hash,
        "transcript_sha256": transcript_sha256,
        "miner_policy": current_policy,
    }).encode("utf-8"))


def build_inventory(memory_system, projects_root):
    """Return a transcript-body-free immutable inventory manifest."""
    memory_system = os.path.realpath(memory_system)
    projects_root = os.path.realpath(projects_root)
    runtime_policy_files = _verify_runtime_policy(memory_system)
    events = os.path.join(memory_system, "events")
    sources = (("main", os.path.join(events, "m3_acquisition_dark.jsonl")),
               ("agent", os.path.join(events, "m3_agent_dark.jsonl")))
    manifest_rows, source_meta = [], []
    logical_ids, transcript_ids = Counter(), Counter()
    resolution = Counter()
    resolution_cache = {}
    transcript_meta_cache = {}
    current_policy = _miner.MINER_POLICY_VERSION

    for lane, path in sources:
        rows, malformed = _read_jsonl(path)
        meta = _artifact_meta(path)
        meta.update({
            "lane": lane,
            "outcome_distribution": dict(sorted(Counter(
                _legacy_outcome(r) for r in rows).items())),
            "lane_distribution": _distribution(rows, "lane"),
            "legacy_row_count": sum(
                1 for r in rows if r.get("miner_policy") != current_policy),
            "current_policy_row_count": sum(
                1 for r in rows if r.get("miner_policy") == current_policy),
        })
        source_meta.append(meta)
        if not meta.get("exists"):
            continue
        for row_number, row in enumerate(rows, 1):
            if row.get("miner_policy") == current_policy:
                continue
            row_hash = _source_hash(row)
            logical = "%s:%s:%s" % (
                lane, row.get("session_id") or "", row.get("claim") or "")
            logical_hash = _sha_bytes(logical.encode("utf-8"))
            logical_ids[logical_hash] += 1
            resolve_key = (lane, str(row.get("session_id") or ""),
                           str(row.get("project_slug") or ""),
                           str(row.get("agent_file") or ""))
            if resolve_key not in resolution_cache:
                resolution_cache[resolve_key] = _resolve_transcripts(
                    row, lane, projects_root)
            matches = resolution_cache[resolve_key]
            if not matches:
                transcript = {"status": "missing", "candidate_count": 0}
            elif len(matches) > 1:
                transcript = {
                    "status": "ambiguous", "candidate_count": len(matches),
                    "candidate_path_hashes": [
                        _sha_bytes(p.encode("utf-8")) for p in matches],
                }
            else:
                tpath = matches[0]
                if tpath not in transcript_meta_cache:
                    st = os.stat(tpath)
                    transcript_meta_cache[tpath] = {
                        "status": "resolved", "candidate_count": 1,
                        "path": tpath, "sha256": _sha_file(tpath),
                        "bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
                    }
                transcript = dict(transcript_meta_cache[tpath])
                transcript_ids[transcript["sha256"]] += 1
            resolution[transcript["status"]] += 1
            replay_id = _replay_row_id(
                path, row_number, row_hash, transcript.get("sha256"), current_policy)
            manifest_rows.append({
                "replay_row_id": replay_id,
                "lane": lane,
                "source_artifact": os.path.realpath(path),
                "source_file_sha256": meta["sha256"],
                "source_row_number": row_number,
                "source_row_hash": row_hash,
                "source_logical_id_hash": logical_hash,
                "session_id": str(row.get("session_id") or ""),
                "project_slug": str(row.get("project_slug") or ""),
                "agent_file": str(row.get("agent_file") or ""),
                "original_policy": row.get("miner_policy"),
                "original_outcome": _legacy_outcome(row),
                "transcript": transcript,
            })

    derived_names = (
        "m3_commission.jsonl", "m3_commission_raw.jsonl", "m3_judged.jsonl",
        "m3_agent_files.jsonl",
    )
    derived = [_artifact_meta(os.path.join(events, name)) for name in derived_names]
    derived.extend(_artifact_meta(path) for path in sorted(glob.glob(
        os.path.join(events, "m3_commission_manifest.*.json"))))
    return {
        "schema": INVENTORY_SCHEMA,
        "created_at": _now_iso(),
        "repo_head": _git_head(os.path.dirname(_BIN)),
        "memory_system": memory_system,
        "projects_root": projects_root,
        "policy_identity": _policy_identity(),
        "installed_runtime_policy_files": runtime_policy_files,
        "sources": source_meta,
        "derived_artifacts": derived,
        "rows": manifest_rows,
        "totals": {
            "legacy_rows": len(manifest_rows),
            "lane_distribution": dict(sorted(Counter(
                r["lane"] for r in manifest_rows).items())),
            "policy_distribution": dict(sorted(Counter(
                "<missing>" if r["original_policy"] is None
                else str(r["original_policy"]) for r in manifest_rows).items())),
            "outcome_distribution": dict(sorted(Counter(
                r["original_outcome"] for r in manifest_rows).items())),
            "source_resolvability": dict(sorted(resolution.items())),
            "duplicate_source_rows": sum(v - 1 for v in logical_ids.values() if v > 1),
            "duplicate_transcript_references": sum(
                v - 1 for v in transcript_ids.values() if v > 1),
            "unique_resolved_transcripts": len(transcript_ids),
        },
    }


def render_inventory_report(manifest):
    totals = manifest["totals"]
    lines = [
        "# M3 legacy inventory baseline", "",
        "This report is aggregate-only. It contains no claims, quotes, transcript bodies, or secrets.", "",
        "- Created: `%s`" % manifest["created_at"],
        "- Repository HEAD: `%s`" % (manifest.get("repo_head") or "unknown"),
        "- Current miner policy: `%s`" % manifest["policy_identity"]["miner_policy"],
        "- Legacy rows: **%d**" % totals["legacy_rows"],
        "- Unique resolved transcripts: **%d**" % totals["unique_resolved_transcripts"],
        "- Duplicate source rows: **%d**" % totals["duplicate_source_rows"],
        "- Duplicate transcript references: **%d**" % totals["duplicate_transcript_references"],
        "", "## Reconciliation", "",
        "- Lanes: `%s`" % _canonical(totals["lane_distribution"]),
        "- Original policies: `%s`" % _canonical(totals["policy_distribution"]),
        "- Original outcomes: `%s`" % _canonical(totals["outcome_distribution"]),
        "- Transcript resolvability: `%s`" % _canonical(totals["source_resolvability"]),
        "", "## Source artifacts", "",
    ]
    for source in manifest["sources"]:
        lines.append("- `%s`: rows=%s legacy=%s malformed=%s sha256=`%s`" % (
            source["lane"], source.get("row_count", 0),
            source.get("legacy_row_count", 0), source.get("malformed_count", 0),
            source.get("sha256", "missing")))
    lines += ["", "## Safety status", "",
              "- Inventory is read-only.",
              "- Replay rows are excluded from live M3 paths and activation gates.",
              "- Missing or ambiguous transcripts remain fail-closed.", ""]
    return "\n".join(lines)


def _load_manifest(path, require_current_policy=True):
    _reject_symlink(path)
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    if not isinstance(manifest, dict) or manifest.get("schema") != INVENTORY_SCHEMA:
        raise ReplayError("invalid legacy inventory schema")
    if require_current_policy and manifest.get("policy_identity") != _policy_identity():
        raise ReplayError("current miner/policy code differs from the inventory")
    rows = manifest.get("rows")
    if not isinstance(rows, list) or any(
            not isinstance(r, dict) or not r.get("replay_row_id") for r in rows):
        raise ReplayError("inventory rows are invalid")
    current_policy = (manifest.get("policy_identity") or {}).get("miner_policy")
    for row in rows:
        expected = _replay_row_id(
            row.get("source_artifact"), row.get("source_row_number"),
            row.get("source_row_hash"),
            (row.get("transcript") or {}).get("sha256"), current_policy)
        if row.get("replay_row_id") != expected:
            raise ReplayError("inventory row identity is invalid")
    return manifest


def _load_source_row(meta):
    rows, malformed = _read_jsonl(meta["source_artifact"])
    number = int(meta["source_row_number"])
    if number < 1 or number > len(rows):
        raise ReplayError("source row is missing")
    row = rows[number - 1]
    if _source_hash(row) != meta["source_row_hash"]:
        raise ReplayError("source row hash changed")
    return row


def _tokens(text):
    return set(_judge._norm_tokens(text or "").split())


def _similarity(left, right, ignore_negation=False):
    a, b = _tokens(left), _tokens(right)
    if ignore_negation:
        a -= _NEGATIONS
        b -= _NEGATIONS
    union = a | b
    return (len(a & b) / len(union)) if union else 0.0


def _polarity(text):
    return bool(_tokens(text) & _NEGATIONS)


def _select_candidate(old_claim, candidates):
    acq = [c for c in candidates if c.get("kind") in _miner.ACQ_KINDS]
    if not acq:
        return None, "current_miner_no_acquisition_candidate", []
    scored = sorted(((round(_similarity(old_claim, c.get("claim")), 6), i, c)
                     for i, c in enumerate(acq)), reverse=True)
    if len(scored) == 1:
        if scored[0][0] >= SIMILARITY_FLOOR:
            return scored[0][2], None, [{"score": scored[0][0]}]
        return None, "singleton_candidate_below_similarity_floor", [
            {"score": scored[0][0]}]
    top, second = scored[0], scored[1]
    evidence = [{"score": score} for score, _i, _c in scored]
    if top[0] >= SIMILARITY_FLOOR and top[0] - second[0] >= SIMILARITY_MARGIN:
        return top[2], None, evidence
    return None, "ambiguous_current_candidates", evidence


def _contains_secret(text):
    return any(pattern.search(text or "") for pattern in _SECRET_PATTERNS)


def _cache_candidates(candidates):
    """Return a minimal cache payload, or None when any body looks secret-like."""
    safe = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        compact = {key: candidate.get(key) for key in (
            "kind", "claim", "transcript_quote", "recall_query", "recalled_answer",
            "miner_policy", "session_id", "project_slug") if key in candidate}
        if _contains_secret(_canonical(compact)):
            return None
        safe.append(compact)
    return safe


def _cache_meta(meta):
    allowed = ("turns", "excerpt_chars", "raw_candidates", "kept", "raw_by_kind",
               "kept_by_kind", "dropped_unknown_kind", "miner_policy", "error")
    return {key: meta.get(key) for key in allowed if key in meta}


def _base_record(meta, manifest_hash):
    return {
        "schema": RESULT_SCHEMA,
        "record_type": "result",
        "origin": "legacy_replay",
        "manifest_sha256": manifest_hash,
        "replay_row_id": meta["replay_row_id"],
        "source_artifact": meta["source_artifact"],
        "source_file_sha256": meta["source_file_sha256"],
        "source_row_number": meta["source_row_number"],
        "source_row_hash": meta["source_row_hash"],
        "lane": meta["lane"],
        "original_policy": meta.get("original_policy"),
        "current_miner_policy": _miner.MINER_POLICY_VERSION,
        "replay_policy": REPLAY_POLICY,
        "replay_timestamp": _now_iso(),
        "transcript_path": (meta.get("transcript") or {}).get("path"),
        "transcript_sha256": (meta.get("transcript") or {}).get("sha256"),
        "deterministic_rail": {"outcome": None, "reason": None},
        "judge": {"invoked": False, "route": None, "model": None,
                  "outcome": None, "quote_verified": False},
        "error": None,
    }


def _finish(record, outcome, reason, error=None):
    if outcome not in ALL_OUTCOMES:
        raise ReplayError("invalid replay outcome")
    record["final_outcome"] = outcome
    record["final_reason"] = reason
    record["error"] = error
    return record


def _side_effect_free_judge_verdict(claim, spans):
    """Run the current judge while redirecting its live audit side effect.

    Replay's own result record is the audit surface. The judge's normal `_log`
    writes claim/quote bodies to the live events directory and is therefore not
    permitted in this isolated path.
    """
    with _JUDGE_CALL_LOCK:
        original_log = _judge._log
        _judge._log = lambda _message: None
        try:
            return _judge.verdict(claim, spans)
        finally:
            _judge._log = original_log


def _evaluate(meta, candidates, miner_meta, enable_judge, manifest_hash):
    record = _base_record(meta, manifest_hash)
    transcript = meta.get("transcript") or {}
    status = transcript.get("status")
    if status == "missing":
        return _finish(record, "source_missing", "transcript_not_found")
    if status != "resolved" or not transcript.get("path"):
        return _finish(record, "invalid_evidence", "ambiguous_transcript_provenance")
    path = transcript["path"]
    if not os.path.isfile(path):
        return _finish(record, "source_missing", "transcript_disappeared")
    if _sha_file(path) != transcript.get("sha256"):
        return _finish(record, "invalid_evidence", "transcript_hash_changed")
    if miner_meta.get("error"):
        return _finish(record, "transient_retry", "miner_unavailable",
                       {"class": str(miner_meta.get("error"))[:160]})
    try:
        old_row = _load_source_row(meta)
    except ReplayError as exc:
        return _finish(record, "invalid_evidence", "source_row_changed",
                       {"class": str(exc)})
    old_claim = str(old_row.get("claim") or "").strip()
    if not old_claim:
        return _finish(record, "invalid_evidence", "legacy_claim_missing")
    candidate, selection_error, selection = _select_candidate(old_claim, candidates)
    record["candidate_selection"] = selection
    if selection_error == "current_miner_no_acquisition_candidate":
        return _finish(record, "rejected_noise", selection_error)
    if selection_error:
        return _finish(record, "conflict_manual_review", selection_error)
    claim = str(candidate.get("claim") or "").strip()
    quote = str(candidate.get("transcript_quote") or "").strip()
    record.update({"kind": candidate.get("kind"), "claim": claim,
                   "supporting_quote": quote})
    if _contains_secret(claim) or _contains_secret(quote):
        record.pop("claim", None)
        record.pop("supporting_quote", None)
        return _finish(record, "invalid_evidence", "secret_bearing_candidate")
    reject = _miner.durability_reject_reason(candidate)
    if reject:
        record["deterministic_rail"] = {"outcome": "rejected", "reason": reject}
        return _finish(record, "rejected_noise", reject)
    record["deterministic_rail"] = {"outcome": "survived", "reason": None}
    try:
        turns = _miner.read_turns(path)
    except Exception as exc:
        return _finish(record, "invalid_evidence", "transcript_parse_failed",
                       {"class": type(exc).__name__})
    if _sha_file(path) != transcript.get("sha256"):
        return _finish(record, "invalid_evidence",
                       "transcript_changed_during_quote_verification")
    quote_ok = any(role == "assistant" and quote in text for role, text in turns or [])
    record["judge"]["quote_verified"] = bool(quote_ok)
    if not quote_ok:
        return _finish(record, "invalid_evidence", "quote_not_in_transcript")
    if not enable_judge:
        return _finish(record, "transient_retry", "judge_not_enabled")
    record["judge"].update({
        "invoked": True,
        "route": str(getattr(_judge, "_JUDGE_PROVIDER", "")),
        "model": str(getattr(_judge, "_JUDGE_MODEL", "")),
    })
    try:
        verdict = _side_effect_free_judge_verdict(claim, [quote])
    except Exception as exc:
        verdict = "judge_unavailable"
        record["error"] = {"class": type(exc).__name__}
    record["judge"]["outcome"] = verdict
    if verdict == "entailed":
        return _finish(record, "salvaged_current_policy", "entailed_quote_verified")
    if verdict == "not_entailed":
        return _finish(record, "invalid_evidence", "judge_not_entailed")
    return _finish(record, "transient_retry", "judge_unavailable_or_malformed")


class _MineCache:
    def __init__(self, cache_dir):
        self._lock = threading.Lock()
        self._items = {}
        self._cache_dir = cache_dir

    def _path(self, key):
        identity = _sha_bytes((str(key) + "\n" + _miner.MINER_POLICY_VERSION)
                              .encode("utf-8"))
        return os.path.join(self._cache_dir, identity + ".json")

    def get(self, meta):
        transcript = meta.get("transcript") or {}
        if transcript.get("status") != "resolved" or not transcript.get("path"):
            return [], {"error": None}
        if not os.path.isfile(transcript["path"]) or \
                _sha_file(transcript["path"]) != transcript.get("sha256"):
            return [], {"error": "transcript_snapshot_changed_before_mining"}
        key = transcript.get("sha256") or transcript["path"]
        with self._lock:
            if key in self._items:
                return self._items[key]
        cache_path = self._path(key)
        if os.path.lexists(cache_path):
            try:
                _reject_symlink(cache_path)
                with open(cache_path, encoding="utf-8") as fh:
                    cached = json.load(fh)
                if cached.get("schema") != MINER_CACHE_SCHEMA or \
                        cached.get("transcript_sha256") != transcript.get("sha256") or \
                        cached.get("miner_policy") != _miner.MINER_POLICY_VERSION or \
                        not isinstance(cached.get("candidates"), list) or \
                        not isinstance(cached.get("meta"), dict):
                    raise ValueError("cache contract mismatch")
                value = (cached["candidates"], cached["meta"])
            except Exception as exc:
                value = ([], {"error": "miner_cache_invalid:%s" %
                              type(exc).__name__})
            with self._lock:
                self._items.setdefault(key, value)
                return self._items[key]
        try:
            value = _miner.mine_transcript(
                transcript["path"], session_id=meta.get("session_id"),
                project_slug=meta.get("project_slug") or "")
        except Exception as exc:
            value = ([], {"error": "miner_exception:%s" % type(exc).__name__})
        if not os.path.isfile(transcript["path"]) or \
                _sha_file(transcript["path"]) != transcript.get("sha256"):
            return [], {"error": "transcript_changed_during_mining"}
        cache_candidates = _cache_candidates(value[0])
        if not value[1].get("error") and cache_candidates is not None:
            _atomic_json(cache_path, {
                "schema": MINER_CACHE_SCHEMA,
                "created_at": _now_iso(),
                "transcript_sha256": transcript.get("sha256"),
                "miner_policy": _miner.MINER_POLICY_VERSION,
                "candidates": cache_candidates,
                "meta": _cache_meta(value[1]),
            })
        with self._lock:
            self._items.setdefault(key, value)
            return self._items[key]


def _production_documents(roots):
    docs = []
    for root in roots:
        for path in sorted(glob.glob(os.path.join(os.path.realpath(root), "**", "*.md"),
                                     recursive=True)):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text)
                      if chunk.strip()]
            for chunk in chunks:
                chunk = chunk[:5000]
                docs.append((path, chunk, _tokens(chunk), _polarity(chunk)))
    return docs


def _relation(claim, accepted, production):
    norm = _judge._norm_tokens(claim)
    for prior in accepted:
        prior_claim = prior.get("claim") or ""
        if norm == _judge._norm_tokens(prior_claim):
            return "duplicate_replay", prior.get("replay_row_id")
        if _polarity(claim) != _polarity(prior_claim) and \
                _similarity(claim, prior_claim, ignore_negation=True) >= CONFLICT_FLOOR:
            return "conflict_replay", prior.get("replay_row_id")
    claim_tokens = _tokens(claim)
    for path, text, tokens, polarity in production:
        if norm and norm in _judge._norm_tokens(text):
            return "duplicate_production", _sha_bytes(path.encode("utf-8"))
        union = (claim_tokens - _NEGATIONS) | (tokens - _NEGATIONS)
        score = (len((claim_tokens - _NEGATIONS) & (tokens - _NEGATIONS)) /
                 len(union)) if union else 0.0
        if polarity != _polarity(claim) and score >= CONFLICT_FLOOR:
            return "conflict_production", _sha_bytes(path.encode("utf-8"))
    return None, None


def _manifest_hash(manifest):
    return _sha_bytes(_canonical(manifest).encode("utf-8"))


def _canonical_production_roots(projects_root, extra_roots=()):
    roots = list(glob.glob(os.path.join(projects_root, "*", "memory")))
    agent_memory = os.path.realpath(os.path.expanduser("~/.claude/agent-memory"))
    if os.path.isdir(agent_memory):
        roots.append(agent_memory)
    roots.extend(extra_roots or ())
    return tuple(sorted(set(os.path.realpath(root) for root in roots if root)))


@contextlib.contextmanager
def _output_lock(output_dir):
    lock_path = os.path.join(output_dir, ".replay.lock")
    _reject_symlink(lock_path)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ReplayError("cannot securely open replay lock: %s" %
                          type(exc).__name__) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReplayError("replay lock is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _bind_output_manifest(output_dir, manifest_hash):
    binding_path = os.path.join(output_dir, "manifest-binding.json")
    existing_artifacts = (
        os.path.join(output_dir, "replay-results.jsonl"),
        os.path.join(output_dir, "checkpoint.json"),
        os.path.join(output_dir, "summary.json"),
        os.path.join(output_dir, "miner-cache"),
    )
    if not os.path.lexists(binding_path):
        if any(os.path.lexists(path) for path in existing_artifacts):
            raise ReplayError("existing replay output lacks an immutable manifest binding")
        _atomic_json(binding_path, {
            "schema": BINDING_SCHEMA,
            "created_at": _now_iso(),
            "manifest_sha256": manifest_hash,
        })
    else:
        _reject_symlink(binding_path)
        try:
            with open(binding_path, encoding="utf-8") as fh:
                binding = json.load(fh)
        except Exception as exc:
            raise ReplayError("manifest binding is unreadable") from exc
        if binding.get("schema") != BINDING_SCHEMA or \
                binding.get("manifest_sha256") != manifest_hash:
            raise ReplayError("replay output is bound to a different manifest")
    checkpoint_path = os.path.join(output_dir, "checkpoint.json")
    if os.path.lexists(checkpoint_path):
        _reject_symlink(checkpoint_path)
        try:
            with open(checkpoint_path, encoding="utf-8") as fh:
                checkpoint = json.load(fh)
        except Exception as exc:
            raise ReplayError("checkpoint is unreadable") from exc
        if checkpoint.get("schema") != CHECKPOINT_SCHEMA or \
                checkpoint.get("manifest_sha256") != manifest_hash:
            raise ReplayError("checkpoint is bound to a different manifest")


def _latest_results(path, manifest_hash=None):
    _reject_symlink(path)
    rows, malformed = _read_jsonl(path)
    latest, terminals = {}, {}
    for row in rows:
        if row.get("record_type") in ("result", "recovery_marker") and \
                manifest_hash is not None and \
                row.get("manifest_sha256") != manifest_hash:
            raise ReplayError("replay result is bound to a different manifest")
        if row.get("record_type") != "result" or not row.get("replay_row_id"):
            continue
        rid = row["replay_row_id"]
        latest[rid] = row
        if row.get("final_outcome") in TERMINAL_OUTCOMES:
            terminals[rid] = row
    return latest, terminals, malformed


def _verify_survivor_binding(meta, result):
    if result.get("source_artifact") != meta.get("source_artifact") or \
            result.get("source_row_hash") != meta.get("source_row_hash") or \
            result.get("transcript_sha256") != \
            (meta.get("transcript") or {}).get("sha256"):
        raise ReplayError("terminal survivor identity differs from manifest")
    _load_source_row(meta)
    transcript = meta.get("transcript") or {}
    path = transcript.get("path")
    if transcript.get("status") != "resolved" or not path or not os.path.isfile(path) or \
            _sha_file(path) != transcript.get("sha256"):
        raise ReplayError("terminal survivor transcript no longer matches manifest")
    try:
        turns = _miner.read_turns(path)
    except Exception as exc:
        raise ReplayError("terminal survivor transcript is unreadable") from exc
    quote = str(result.get("supporting_quote") or "")
    if not quote or not any(role == "assistant" and quote in text
                            for role, text in turns or []):
        raise ReplayError("terminal survivor quote no longer verifies exactly")
    if _contains_secret(str(result.get("claim") or "")) or _contains_secret(quote):
        raise ReplayError("terminal survivor contains secret-like material")


def _summary_for(selected, results_path, manifest_hash):
    latest, terminals, malformed = _latest_results(results_path, manifest_hash)
    ids = [r["replay_row_id"] for r in selected]
    terminal = {rid: terminals[rid] for rid in ids if rid in terminals}
    retries = {rid: latest[rid] for rid in ids
               if rid not in terminal and latest.get(rid, {}).get("final_outcome") ==
               "transient_retry"}
    pending = [rid for rid in ids if rid not in terminal and rid not in retries]
    outcomes = Counter(r["final_outcome"] for r in terminal.values())
    reasons = Counter(r.get("final_reason") or "<missing>"
                      for r in list(terminal.values()) + list(retries.values()))
    reconciled = len(ids) == len(terminal) + len(retries) + len(pending)
    return {
        "schema": "m3-legacy-replay-summary-v1",
        "generated_at": _now_iso(),
        "selected_input_count": len(ids),
        "terminal_count": len(terminal),
        "nonterminal_retry_count": len(retries),
        "pending_count": len(pending),
        "terminal_outcomes": dict(sorted(outcomes.items())),
        "reason_distribution": dict(sorted(reasons.items())),
        "malformed_result_lines_preserved": len(malformed),
        "reconciled": reconciled,
    }


def _select_rows(rows, one_per_transcript=False):
    if not one_per_transcript:
        return list(rows)
    selected, seen = [], set()
    for row in rows:
        transcript = row.get("transcript") or {}
        key = transcript.get("sha256") or (
            transcript.get("status"), row.get("session_id"), row.get("agent_file"))
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def run_replay(manifest, output_dir, offset=0, limit=None, workers=1,
               enable_judge=False, production_roots=(), one_per_transcript=False):
    memory_system = manifest["memory_system"]
    projects_root = manifest["projects_root"]
    production_roots = _canonical_production_roots(projects_root, production_roots)
    _guard_output(output_dir, memory_system, projects_root, production_roots)
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    with _output_lock(output_dir):
        return _run_replay_locked(
            manifest, output_dir, offset=offset, limit=limit, workers=workers,
            enable_judge=enable_judge, production_roots=production_roots,
            one_per_transcript=one_per_transcript)


def _run_replay_locked(manifest, output_dir, offset=0, limit=None, workers=1,
                       enable_judge=False, production_roots=(),
                       one_per_transcript=False):
    manifest_hash = _manifest_hash(manifest)
    _bind_output_manifest(output_dir, manifest_hash)
    results_path = os.path.join(output_dir, "replay-results.jsonl")
    checkpoint_path = os.path.join(output_dir, "checkpoint.json")
    summary_path = os.path.join(output_dir, "summary.json")
    cache_dir = os.path.join(output_dir, "miner-cache")
    for artifact in (results_path, checkpoint_path, summary_path, cache_dir):
        _reject_symlink(artifact)
    _prepare_append(results_path, manifest_hash)
    rows = _select_rows(manifest["rows"], one_per_transcript)
    selected = rows[max(0, offset):]
    if limit is not None:
        selected = selected[:max(0, limit)]
    workers = int(workers)
    if workers != MAX_WORKERS:
        raise ReplayError(
            "only workers=1 is admitted; parallel model calls require the shared "
            "adaptive worker contract and a same-task capacity smoke")
    _latest, terminals, _malformed = _latest_results(results_path, manifest_hash)
    selected_by_id = {row["replay_row_id"]: row for row in selected}
    for rid, result in terminals.items():
        if rid in selected_by_id and \
                result.get("final_outcome") == "salvaged_current_policy":
            _verify_survivor_binding(selected_by_id[rid], result)
    pending = [r for r in selected if r["replay_row_id"] not in terminals]
    cache = _MineCache(cache_dir)

    def work(meta):
        candidates, miner_meta = cache.get(meta)
        return meta, _evaluate(
            meta, candidates, miner_meta, enable_judge, manifest_hash)

    evaluated = [work(meta) for meta in pending]
    order = {r["replay_row_id"]: i for i, r in enumerate(selected)}
    evaluated.sort(key=lambda pair: order[pair[0]["replay_row_id"]])
    production = _production_documents(production_roots)
    accepted = [row for row in terminals.values()
                if row.get("final_outcome") == "salvaged_current_policy"]
    for _meta, record in evaluated:
        if record.get("final_outcome") == "salvaged_current_policy":
            relation, related = _relation(record.get("claim") or "", accepted, production)
            if relation:
                record["relation"] = {"type": relation, "related_id": related}
                record = _finish(record, "conflict_manual_review", relation)
            else:
                accepted.append(record)
        _append_record(results_path, record)

    summary = _summary_for(selected, results_path, manifest_hash)
    latest, completed, malformed = _latest_results(results_path, manifest_hash)
    checkpoint = {
        "schema": CHECKPOINT_SCHEMA,
        "updated_at": _now_iso(),
        "manifest_sha256": manifest_hash,
        "selected_offset": offset,
        "selected_limit": limit,
        "one_per_transcript": bool(one_per_transcript),
        "judge_enabled": bool(enable_judge),
        "workers": workers,
        "completed_replay_row_ids": sorted(
            rid for rid in completed if rid in order),
        "retry_replay_row_ids": sorted(
            rid for rid, row in latest.items() if rid in order and
            rid not in completed and row.get("final_outcome") == "transient_retry"),
        "malformed_result_lines_preserved": len(malformed),
    }
    _atomic_json(summary_path, summary)
    _atomic_json(checkpoint_path, checkpoint)
    return summary, results_path


def _safe_pointer(row):
    return "%s#row-%s" % (os.path.basename(row.get("source_artifact") or "source"),
                           row.get("source_row_number") or "?")


def _review_claim(text):
    clean = " ".join(str(text or "").split())[:1000]
    return clean.replace("`", "\\`")


def render_review_packet(manifest, results_path, sample_size=5):
    latest, terminals, malformed = _latest_results(
        results_path, _manifest_hash(manifest))
    input_ids = [r["replay_row_id"] for r in manifest["rows"]]
    terminal_rows = [terminals[rid] for rid in input_ids if rid in terminals]
    retry_rows = [latest[rid] for rid in input_ids
                  if rid not in terminals and rid in latest and
                  latest[rid].get("final_outcome") == "transient_retry"]
    pending = len(input_ids) - len(terminal_rows) - len(retry_rows)
    outcomes = Counter(r["final_outcome"] for r in terminal_rows)
    reasons = Counter(r.get("final_reason") or "<missing>"
                      for r in terminal_rows + retry_rows)
    survivors = [r for r in terminal_rows
                 if r.get("final_outcome") == "salvaged_current_policy"]
    manifest_by_id = {row["replay_row_id"]: row for row in manifest["rows"]}
    for survivor in survivors:
        _verify_survivor_binding(
            manifest_by_id[survivor["replay_row_id"]], survivor)
    failures = [r for r in terminal_rows
                if r.get("final_outcome") in ("source_missing", "invalid_evidence")]
    conflicts = [r for r in terminal_rows
                 if r.get("final_outcome") == "conflict_manual_review"]
    rejects = [r for r in terminal_rows
               if r.get("final_outcome") == "rejected_noise"]
    reconciled = len(input_ids) == len(terminal_rows) + len(retry_rows) + pending
    lines = [
        "# M3 legacy replay human review packet", "",
        "Replay-only artifact. Nothing listed here has been imported into production memory.", "",
        "## Accounting", "",
        "- Manifest inputs: **%d**" % len(input_ids),
        "- Terminal: **%d**" % len(terminal_rows),
        "- Non-terminal retries: **%d**" % len(retry_rows),
        "- Pending/unprocessed: **%d**" % pending,
        "- Reconciled: **%s**" % str(reconciled).lower(),
        "- Preserved malformed result lines: **%d**" % len(malformed),
        "- Outcomes: `%s`" % _canonical(dict(sorted(outcomes.items()))),
        "- Reasons: `%s`" % _canonical(dict(sorted(reasons.items()))),
        "", "## Source/provenance failures", "",
    ]
    if failures:
        for row in failures:
            lines.append("- `%s` — `%s` — %s" % (
                row["replay_row_id"][:12], row.get("final_reason"), _safe_pointer(row)))
    else:
        lines.append("- None in processed terminal rows.")
    lines += ["", "## Proposed import list (review only)", ""]
    if survivors:
        for i, row in enumerate(survivors, 1):
            lines += [
                "### %d. `%s` [%s]" % (i, row["replay_row_id"][:12], row.get("kind")),
                "", "Claim: %s" % _review_claim(row.get("claim")), "",
                "Exact supporting quote (JSON string): `%s`" %
                _canonical(row.get("supporting_quote") or ""), "",
                "Source: `%s` · transcript sha256 `%s`" % (
                    _safe_pointer(row), row.get("transcript_sha256")),
                "", "Decision: [ ] import proposal accepted  [ ] reject", "",
            ]
    else:
        lines.append("No replay survivor is eligible for review yet.")
    lines += ["", "## Duplicate/conflict groups", ""]
    if conflicts:
        for row in conflicts:
            rel = row.get("relation") or {}
            lines.append("- `%s` — `%s` — related `%s`" % (
                row["replay_row_id"][:12], row.get("final_reason"),
                str(rel.get("related_id") or "manual-review")[:16]))
    else:
        lines.append("- None in processed terminal rows.")
    lines += ["", "## Stratified manual-label sample", "",
              "### Survivors", ""]
    for row in survivors[:sample_size]:
        lines.append("- `%s` — %s" % (row["replay_row_id"][:12], _safe_pointer(row)))
    if not survivors:
        lines.append("- None.")
    lines += ["", "### Rejects", ""]
    by_reason = defaultdict(list)
    for row in rejects:
        by_reason[row.get("final_reason")].append(row)
    sampled = []
    for reason in sorted(by_reason):
        sampled.append(by_reason[reason][0])
        if len(sampled) >= sample_size:
            break
    for row in sampled:
        lines.append("- `%s` — `%s` — %s" % (
            row["replay_row_id"][:12], row.get("final_reason"), _safe_pointer(row)))
    if not sampled:
        lines.append("- None.")
    lines += ["", "## Stop condition", "",
              "No automatic import action exists in this tool. Human review is mandatory.", ""]
    return "\n".join(lines)


def _write_text(path, text):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    _reject_symlink(path)
    fd, tmp = tempfile.mkstemp(prefix=".m3-legacy-", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(text.rstrip() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--memory-system", required=True)
    inv.add_argument("--projects-root", required=True)
    inv.add_argument("--manifest", required=True)
    inv.add_argument("--report", required=True)

    rep = sub.add_parser("replay")
    rep.add_argument("--manifest", required=True)
    rep.add_argument("--output-dir", required=True)
    rep.add_argument("--offset", type=int, default=0)
    rep.add_argument("--limit", type=int, default=None)
    rep.add_argument("--workers", type=int, default=1,
                     help="bounded model-call concurrency; currently admitted cap is 1")
    rep.add_argument("--enable-judge", action="store_true",
                     help="explicit opt-in to current entailment judge calls")
    rep.add_argument("--production-root", action="append", default=[])
    rep.add_argument("--one-per-transcript", action="store_true",
                     help="bounded stratified source selection")
    rep.add_argument("--dry-run", action="store_true", default=True,
                     help="documented default; replay has no production-write mode")

    rev = sub.add_parser("review")
    rev.add_argument("--manifest", required=True)
    rev.add_argument("--results", required=True)
    rev.add_argument("--output", required=True)
    rev.add_argument("--sample-size", type=int, default=5)
    args = parser.parse_args(argv)

    if args.command == "inventory":
        _guard_distinct_output(args.manifest, (args.report,))
        _guard_output(args.manifest, args.memory_system, args.projects_root)
        _guard_output(args.report, args.memory_system, args.projects_root)
        manifest = build_inventory(args.memory_system, args.projects_root)
        _atomic_json(args.manifest, manifest)
        _write_text(args.report, render_inventory_report(manifest))
        print(_canonical({"status": "ok", "dry_run": True,
                          "manifest": args.manifest, "totals": manifest["totals"]}))
        return 0
    if args.command == "replay":
        manifest = _load_manifest(args.manifest)
        summary, results = run_replay(
            manifest, args.output_dir, offset=args.offset, limit=args.limit,
            workers=args.workers, enable_judge=args.enable_judge,
            production_roots=args.production_root,
            one_per_transcript=args.one_per_transcript)
        print(_canonical({"status": "ok", "dry_run": True,
                          "judge_enabled": args.enable_judge,
                          "results": results, "summary": summary}))
        return 0
    manifest = _load_manifest(args.manifest, require_current_policy=False)
    _guard_output(args.output, manifest["memory_system"], manifest["projects_root"],
                  _canonical_production_roots(manifest["projects_root"]))
    results_dir = os.path.dirname(os.path.realpath(args.results))
    _guard_distinct_output(
        args.output,
        (args.manifest, args.results,
         os.path.join(results_dir, "manifest-binding.json"),
         os.path.join(results_dir, "checkpoint.json"),
         os.path.join(results_dir, "summary.json"),
         os.path.join(results_dir, "inventory-report.md")),
        (os.path.join(results_dir, "miner-cache"),))
    packet = render_review_packet(manifest, args.results, args.sample_size)
    _write_text(args.output, packet)
    print(_canonical({"status": "ok", "dry_run": True, "output": args.output}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
