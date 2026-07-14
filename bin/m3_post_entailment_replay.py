#!/usr/bin/env python3
"""Offline post-entailment routing for bound M3 review candidates.

Phase A is deliberately evidence-plan driven.  It verifies immutable upstream
candidate replay records, hashes explicitly selected stronger sources, checks
later-turn assertions, and derives a destination without writing to memory,
project trackers, incident archives, D5, or live M3 files.

The tool has no provider call, import, deployment, or destination-write mode.
Weak or unavailable retrieval can only produce manual review.
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter, defaultdict


_BIN = os.path.dirname(os.path.abspath(__file__))
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

import m3_legacy_candidate_replay as candidate_replay  # noqa: E402
import m3_legacy_replay as legacy  # noqa: E402


MANIFEST_SCHEMA = "m3-post-entailment-manifest-v1"
CATALOG_SCHEMA = "m3-post-entailment-source-catalog-v1"
RESULT_SCHEMA = "m3-post-entailment-result-v1"
CHECKPOINT_SCHEMA = "m3-post-entailment-checkpoint-v1"
BINDING_SCHEMA = "m3-post-entailment-binding-v1"
SUMMARY_SCHEMA = "m3-post-entailment-summary-v1"
TRIAGE_POLICY = "m3-post-entailment-v1"
MAX_WORKERS = 1

TERMINAL_OUTCOMES = frozenset({
    "memory_candidate",
    "project_fix_candidate",
    "incident_evidence_candidate",
    "duplicate_existing",
    "superseded_later_context",
    "rejected_overgeneralized",
    "rejected_volatile_state",
    "invalid_evidence",
    "needs_manual_review",
})
ALL_OUTCOMES = TERMINAL_OUTCOMES | {"transient_retry"}

SOURCE_ROLES = frozenset({
    "stronger_duplicate",
    "current_project_defect",
    "incident_observation",
    "incident_resolution",
})
AUTHORITIES = frozenset({
    "production_memory",
    "canonical_handoff",
    "canonical_spec",
    "current_source",
    "skill",
    "incident_handoff",
    "release_contract",
})
RETRIEVAL_MODES = frozenset({"hybrid", "fts_only", "unavailable"})
RETRIEVAL_STATUSES = frozenset({"duplicate", "distinct", "uncertain"})

_EVALUATIVE_RE = re.compile(
    r"(?:\bmore\s+(?:accurate|valuable)\b|\bproved\s+more\b|"
    r"\bточн\w*\b|\bценн\w*\b)", re.IGNORECASE)
_NEAR_ELIMINATION_RE = re.compile(
    r"(?:\bnearly\s+eliminat\w*\b|\bnearly\s+zero\b|"
    r"\bпочти\s+обнул\w*\b|\bпрактически\s+исчез\w*\b)", re.IGNORECASE)
_ZERO_OBSOLESCENCE_RE = re.compile(
    r"(?:obsolesc\w*|staleness|протухан\w*|устареван\w*)"
    r".{0,48}(?:≈|~=|approximately|примерно|почти)?\s*0(?:\b|[^-9])",
    re.IGNORECASE | re.DOTALL)
_RUNTIME_RE = re.compile(
    r"(?:\bRAM\b|\bkernel\b|\bprocess\b|\bholding\b|\bavailable\s+ram\b|"
    r"\bversion\b|\bbuild\b|\bGB\b|\bMB\b|\bГБ\b|\bМБ\b|"
    r"\bзапущ\w*\b|\bдержит\b)", re.IGNORECASE)
_CONFIG_RE = re.compile(
    r"(?:\bprovider\b|\bmodel\b|\broute\b|\brouting\b|\beffort\b|"
    r"\btimeout\b|\bavailability\b|\bavailable\b|\bcouncil\.toml\b|"
    r"\bмаршрут\w*\b|\bуровн\w*\s+effort\b)", re.IGNORECASE)
_PROJECT_STATE_RE = re.compile(
    r"(?:\bBLOCKER\b|\bMAJOR\b|\bMINOR\b|\bNOTE\b|\bcompleted\b|"
    r"\bfixed\b|\bverified\b|\bdeployed\b|\bimplemented\b|"
    r"\bисправ\w*\b|\bзаверш\w*\b|\bверифиц\w*\b)", re.IGNORECASE)


class PostEntailmentError(legacy.ReplayError):
    """A fail-closed post-entailment contract violation."""


def _policy_identity():
    files = {}
    for name in ("m3_post_entailment_replay.py",
                 "m3_legacy_candidate_replay.py", "m3_legacy_replay.py"):
        files[name] = legacy._sha_file(os.path.join(_BIN, name))
    return {
        "triage_policy": TRIAGE_POLICY,
        "candidate_replay_policy": candidate_replay.POLICY,
        "files": files,
        "provider_route": None,
        "provider_calls_admitted": False,
    }


def _load_json(path, label):
    try:
        return candidate_replay._load_json(path, label)
    except candidate_replay.CandidateReplayError as exc:
        raise PostEntailmentError(str(exc)) from exc


def _regular_bytes(path, label):
    legacy._reject_symlink(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PostEntailmentError("%s is unreadable" % label) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PostEntailmentError("%s is not a regular file" % label)
        chunks = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _line_slice(path, start, end):
    if not isinstance(start, int) or not isinstance(end, int) or \
            start < 1 or end < start:
        raise PostEntailmentError("source assertion line range is invalid")
    try:
        text = _regular_bytes(path, "bound source").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PostEntailmentError("bound source is not UTF-8") from exc
    lines = text.splitlines()
    if end > len(lines):
        raise PostEntailmentError("source assertion line range exceeds file")
    return "\n".join(lines[start - 1:end])


def _git_identity(path):
    directory = os.path.dirname(os.path.realpath(path))
    try:
        root = subprocess.check_output(
            ["git", "-C", directory, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True).strip()
        head = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
        return {"root": os.path.realpath(root), "head": head}
    except Exception:
        return {"root": None, "head": None}


def _catalog_rows(catalog):
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise PostEntailmentError("invalid source catalog schema")
    rows = catalog.get("rows")
    if not isinstance(rows, list) or not rows:
        raise PostEntailmentError("source catalog rows are missing")
    if any(not isinstance(row, dict) or not row.get("candidate_id")
           for row in rows):
        raise PostEntailmentError("source catalog row is invalid")
    ids = [row["candidate_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise PostEntailmentError("source catalog candidate IDs are not unique")
    return rows


def _candidate_result_binding(candidate_results_path, manifest_hash):
    result_dir = os.path.dirname(os.path.realpath(candidate_results_path))
    binding_path = os.path.join(result_dir, "candidate-manifest-binding.json")
    binding = _load_json(binding_path, "candidate result binding")
    if binding.get("schema") != candidate_replay.BINDING_SCHEMA or \
            binding.get("manifest_sha256") != manifest_hash:
        raise PostEntailmentError(
            "candidate results are bound to a different candidate manifest")
    return {
        "path": os.path.realpath(binding_path),
        "sha256": legacy._sha_file(binding_path),
    }


def _strict_upstream(meta, result, candidate_manifest_hash):
    quote = result.get("quote_verification") or {}
    judge = result.get("judge") or {}
    rail = result.get("deterministic_rail") or {}
    occurrence = {
        (item.get("cache_path"), item.get("cache_sha256"),
         item.get("candidate_index"))
        for item in (meta.get("source_occurrences") or [])
    }
    selected = (result.get("source_cache_path"),
                result.get("source_cache_sha256"),
                result.get("source_candidate_index"))
    valid = (
        result.get("schema") == candidate_replay.RESULT_SCHEMA and
        result.get("record_type") == "result" and
        result.get("origin") == "legacy_candidate_replay" and
        result.get("candidate_replay_policy") == candidate_replay.POLICY and
        result.get("manifest_sha256") == candidate_manifest_hash and
        result.get("candidate_id") == meta.get("candidate_id") and
        result.get("candidate_body_sha256") ==
        meta.get("candidate_body_sha256") and
        result.get("kind") == meta.get("kind") and
        result.get("miner_policy") == meta.get("miner_policy") and
        result.get("final_outcome") == "review_candidate" and
        rail.get("outcome") == "survived" and
        quote.get("exact") is True and
        isinstance(quote.get("assistant_turn_indices"), list) and
        bool(quote.get("assistant_turn_indices")) and
        judge.get("outcome") == "entailed" and
        judge.get("quote_verified") is True and
        bool(judge.get("invoked") or judge.get("reused")) and
        selected in occurrence
    )
    if not valid:
        raise PostEntailmentError(
            "selected input fails the terminal entailed review contract")
    try:
        candidate_replay._verify_review_candidate(meta, result)
    except candidate_replay.CandidateReplayError as exc:
        raise PostEntailmentError(str(exc)) from exc
    if legacy._contains_secret(legacy._canonical({
            "claim": result.get("claim"),
            "quote": result.get("supporting_quote")})):
        raise PostEntailmentError("selected input contains secret-like material")


def _load_upstream(candidate_inventory_path, candidate_results_path):
    try:
        inventory = candidate_replay._load_inventory(candidate_inventory_path)
    except candidate_replay.CandidateReplayError as exc:
        raise PostEntailmentError(str(exc)) from exc
    candidate_manifest_hash = legacy._manifest_hash(inventory)
    _candidate_result_binding(candidate_results_path, candidate_manifest_hash)
    legacy._reject_symlink(candidate_results_path)
    rows, malformed = legacy._read_jsonl(candidate_results_path)
    if malformed:
        raise PostEntailmentError("candidate results contain malformed lines")
    latest = {}
    for row in rows:
        if row.get("record_type") in ("result", "recovery_marker") and \
                row.get("manifest_sha256") != candidate_manifest_hash:
            raise PostEntailmentError("candidate result manifest binding changed")
        if row.get("record_type") == "result" and row.get("candidate_id"):
            latest[row["candidate_id"]] = row
    return inventory, candidate_manifest_hash, latest


def _normalize_retrieval(value):
    value = value or {}
    if not isinstance(value, dict):
        raise PostEntailmentError("retrieval assertion is invalid")
    mode = value.get("mode", "unavailable")
    status = value.get("status", "uncertain")
    no_confident = value.get("no_confident_results", True)
    if mode not in RETRIEVAL_MODES or status not in RETRIEVAL_STATUSES or \
            not isinstance(no_confident, bool):
        raise PostEntailmentError("retrieval assertion uses an invalid state")
    related = value.get("related_source_ids") or []
    if not isinstance(related, list) or any(not isinstance(item, str)
                                            for item in related):
        raise PostEntailmentError("retrieval related source IDs are invalid")
    # Phase A never treats a catalog assertion as proof of novelty.  Even a
    # bound hybrid snapshot marked distinct stays manual until a real semantic
    # adapter and labeled shadow gate are admitted.
    return {
        "mode": mode,
        "status": status,
        "no_confident_results": no_confident,
        "related_source_ids": sorted(set(related)),
    }


def _normalize_later_assertions(entry, meta, result):
    raw = entry.get("later_turn_assertions") or []
    if not isinstance(raw, list):
        raise PostEntailmentError("later-turn assertions must be a list")
    transcript = meta.get("transcript") or {}
    path = transcript.get("path")
    turns = candidate_replay._full_turns(path)
    quote_indices = result["quote_verification"]["assistant_turn_indices"]
    earliest = min(quote_indices)
    normalized = []
    for assertion in raw:
        if not isinstance(assertion, dict) or \
                assertion.get("relation") not in ("supersedes", "ambiguous"):
            raise PostEntailmentError("later-turn assertion is invalid")
        index = assertion.get("assistant_turn_index")
        expected_hash = assertion.get("text_sha256")
        if not isinstance(index, int) or index <= earliest or index >= len(turns):
            raise PostEntailmentError("later-turn assertion index is invalid")
        role, text = turns[index]
        actual_hash = legacy._sha_bytes(text.encode("utf-8"))
        if role != "assistant" or not expected_hash or actual_hash != expected_hash:
            raise PostEntailmentError("later-turn assertion hash changed")
        normalized.append({
            "assistant_turn_index": index,
            "text_sha256": actual_hash,
            "relation": assertion["relation"],
        })
    return sorted(normalized, key=lambda item: (
        item["assistant_turn_index"], item["text_sha256"]))


def _normalize_source_assertions(entry):
    raw = entry.get("source_assertions") or []
    if not isinstance(raw, list):
        raise PostEntailmentError("source assertions must be a list")
    normalized = []
    for assertion in raw:
        if not isinstance(assertion, dict):
            raise PostEntailmentError("source assertion is invalid")
        supplied_path = os.path.abspath(os.path.expanduser(
            str(assertion.get("path") or "")))
        if os.path.islink(supplied_path):
            raise PostEntailmentError("bound source is missing or linked")
        path = os.path.realpath(supplied_path)
        source_id = str(assertion.get("source_id") or "")
        role = assertion.get("role")
        authority = assertion.get("authority")
        start = assertion.get("line_start")
        end = assertion.get("line_end", start)
        if not source_id or not path or role not in SOURCE_ROLES or \
                authority not in AUTHORITIES:
            raise PostEntailmentError("source assertion contract is incomplete")
        if not os.path.isfile(path):
            raise PostEntailmentError("bound source is missing or linked")
        body = _regular_bytes(path, "bound source")
        selected = _line_slice(path, start, end)
        if not selected.strip():
            raise PostEntailmentError("bound source assertion selects empty text")
        if legacy._contains_secret(selected):
            raise PostEntailmentError(
                "secret-like material found in selected source lines")
        git = _git_identity(path)
        normalized.append({
            "source_id": source_id,
            "path": path,
            "sha256": legacy._sha_bytes(body),
            "bytes": len(body),
            "line_start": start,
            "line_end": end,
            "selected_text_sha256": legacy._sha_bytes(selected.encode("utf-8")),
            "authority": authority,
            "role": role,
            "git_root": git["root"],
            "git_head": git["head"],
        })
    ids = [item["source_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise PostEntailmentError("source assertion IDs are not unique per candidate")
    return sorted(normalized, key=lambda item: item["source_id"])


def _normalize_incident_scope(entry, assertions):
    raw = entry.get("incident_scope")
    incident_roles = {item["role"] for item in assertions} & {
        "incident_observation", "incident_resolution"}
    if raw is None:
        return None
    if not incident_roles or not isinstance(raw, dict):
        raise PostEntailmentError("incident scope is not bound to incident sources")
    observed_at = str(raw.get("observed_at") or "")
    asset_scope = str(raw.get("asset_scope") or "")
    resolution_ids = raw.get("resolution_source_ids") or []
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]+)?", observed_at) or \
            not asset_scope or len(asset_scope) > 240 or \
            legacy._contains_secret(asset_scope) or \
            not isinstance(resolution_ids, list) or \
            any(not isinstance(item, str) for item in resolution_ids):
        raise PostEntailmentError("incident scope contract is invalid")
    roles_by_id = {item["source_id"]: item["role"] for item in assertions}
    if any(roles_by_id.get(item) != "incident_resolution"
           for item in resolution_ids):
        raise PostEntailmentError(
            "incident resolution IDs do not bind resolution sources")
    return {
        "observed_at": observed_at,
        "asset_scope": asset_scope,
        "resolution_source_ids": sorted(set(resolution_ids)),
    }


def _triage_row_id(candidate_id, body_hash, transcript_hash,
                   candidate_manifest_hash, source_snapshot_hash):
    return legacy._sha_bytes(legacy._canonical({
        "candidate_id": candidate_id,
        "candidate_body_sha256": body_hash,
        "transcript_sha256": transcript_hash,
        "candidate_manifest_sha256": candidate_manifest_hash,
        "triage_policy": TRIAGE_POLICY,
        "source_snapshot_sha256": source_snapshot_hash,
    }).encode("utf-8"))


def build_manifest(candidate_inventory_path, candidate_results_path,
                   source_catalog_path):
    inventory, candidate_manifest_hash, results = _load_upstream(
        candidate_inventory_path, candidate_results_path)
    catalog = _load_json(source_catalog_path, "source catalog")
    catalog_rows = _catalog_rows(catalog)
    inventory_by_id = {row["candidate_id"]: row for row in inventory["rows"]}
    manifest_rows = []
    source_files = {}
    for entry in catalog_rows:
        candidate_id = entry["candidate_id"]
        meta = inventory_by_id.get(candidate_id)
        result = results.get(candidate_id)
        if not meta or not result:
            raise PostEntailmentError(
                "source catalog selects a missing candidate result")
        _strict_upstream(meta, result, candidate_manifest_hash)
        assertions = _normalize_source_assertions(entry)
        later = _normalize_later_assertions(entry, meta, result)
        retrieval = _normalize_retrieval(entry.get("retrieval"))
        incident_scope = _normalize_incident_scope(entry, assertions)
        owner_scope = str(entry.get("owner_scope") or "manual")
        snapshot = {
            "source_assertions": assertions,
            "later_turn_assertions": later,
            "retrieval": retrieval,
            "incident_scope": incident_scope,
            "owner_scope": owner_scope,
        }
        snapshot_hash = legacy._sha_bytes(
            legacy._canonical(snapshot).encode("utf-8"))
        transcript = meta["transcript"]
        row = {
            "candidate_id": candidate_id,
            "candidate_body_sha256": meta["candidate_body_sha256"],
            "kind": meta["kind"],
            "miner_policy": meta["miner_policy"],
            "candidate_manifest_sha256": candidate_manifest_hash,
            "candidate_result_record_sha256": legacy._source_hash(result),
            "transcript_path": transcript["path"],
            "transcript_sha256": transcript["sha256"],
            "quote_assistant_turn_indices":
                result["quote_verification"]["assistant_turn_indices"],
            "source_snapshot_sha256": snapshot_hash,
            "source_assertions": assertions,
            "later_turn_assertions": later,
            "retrieval": retrieval,
            "incident_scope": incident_scope,
            "owner_scope": owner_scope,
        }
        row["triage_row_id"] = _triage_row_id(
            candidate_id, row["candidate_body_sha256"],
            row["transcript_sha256"], candidate_manifest_hash, snapshot_hash)
        manifest_rows.append(row)
        for assertion in assertions:
            prior = source_files.get(assertion["path"])
            summary = {key: assertion[key] for key in (
                "path", "sha256", "bytes", "git_root", "git_head")}
            if prior and prior != summary:
                raise PostEntailmentError("bound source has conflicting metadata")
            source_files[assertion["path"]] = summary

    manifest_rows.sort(key=lambda row: row["candidate_id"])
    outcome_roles = Counter(
        assertion["role"] for row in manifest_rows
        for assertion in row["source_assertions"])
    return {
        "schema": MANIFEST_SCHEMA,
        "created_at": legacy._now_iso(),
        "repo_head": legacy._git_head(os.path.dirname(_BIN)),
        "policy_identity": _policy_identity(),
        "memory_system": inventory["memory_system"],
        "projects_root": inventory["projects_root"],
        "candidate_inventory": {
            "path": os.path.realpath(candidate_inventory_path),
            "sha256": legacy._sha_file(candidate_inventory_path),
            "canonical_sha256": candidate_manifest_hash,
        },
        "candidate_results": {
            "path": os.path.realpath(candidate_results_path),
            "sha256": legacy._sha_file(candidate_results_path),
            "binding": _candidate_result_binding(
                candidate_results_path, candidate_manifest_hash),
        },
        "source_catalog": {
            "path": os.path.realpath(source_catalog_path),
            "sha256": legacy._sha_file(source_catalog_path),
        },
        "source_files": sorted(source_files.values(), key=lambda item: item["path"]),
        "rows": manifest_rows,
        "totals": {
            "selected_review_candidates": len(manifest_rows),
            "bound_source_files": len(source_files),
            "bound_source_assertions": sum(
                len(row["source_assertions"]) for row in manifest_rows),
            "later_turn_assertions": sum(
                len(row["later_turn_assertions"]) for row in manifest_rows),
            "source_role_distribution": dict(sorted(outcome_roles.items())),
        },
    }


def _verify_file_binding(meta, label):
    path = meta.get("path") or ""
    if not path or os.path.islink(path) or not os.path.isfile(path) or \
            legacy._sha_file(path) != meta.get("sha256"):
        raise PostEntailmentError("%s changed" % label)


def _load_manifest(path, require_current_policy=True):
    manifest = _load_json(path, "triage manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise PostEntailmentError("invalid triage manifest schema")
    if require_current_policy and manifest.get("policy_identity") != _policy_identity():
        raise PostEntailmentError("triage code or policy differs from manifest")
    rows = manifest.get("rows")
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not row.get("triage_row_id")
            for row in rows):
        raise PostEntailmentError("triage manifest rows are invalid")
    if len({row["triage_row_id"] for row in rows}) != len(rows) or \
            len({row["candidate_id"] for row in rows}) != len(rows):
        raise PostEntailmentError("triage manifest row identities are not unique")
    for key, label in (("candidate_inventory", "candidate inventory"),
                       ("candidate_results", "candidate results"),
                       ("source_catalog", "source catalog")):
        _verify_file_binding(manifest.get(key) or {}, label)
    inventory, candidate_manifest_hash, results = _load_upstream(
        manifest["candidate_inventory"]["path"],
        manifest["candidate_results"]["path"])
    if candidate_manifest_hash != \
            manifest["candidate_inventory"].get("canonical_sha256"):
        raise PostEntailmentError("candidate manifest canonical hash changed")
    inventory_by_id = {row["candidate_id"]: row for row in inventory["rows"]}
    for source in manifest.get("source_files") or []:
        _verify_file_binding(source, "bound source")
        git = _git_identity(source["path"])
        if git.get("root") != source.get("git_root") or \
                git.get("head") != source.get("git_head"):
            raise PostEntailmentError("bound source Git identity changed")
    for row in rows:
        expected = _triage_row_id(
            row.get("candidate_id"), row.get("candidate_body_sha256"),
            row.get("transcript_sha256"),
            row.get("candidate_manifest_sha256"),
            row.get("source_snapshot_sha256"))
        if row.get("triage_row_id") != expected or \
                row.get("candidate_manifest_sha256") != candidate_manifest_hash:
            raise PostEntailmentError("triage row identity is invalid")
        meta = inventory_by_id.get(row["candidate_id"])
        result = results.get(row["candidate_id"])
        if not meta or not result or legacy._source_hash(result) != \
                row.get("candidate_result_record_sha256"):
            raise PostEntailmentError("bound candidate result changed")
        _strict_upstream(meta, result, candidate_manifest_hash)
        snapshot = {
            "source_assertions": row.get("source_assertions") or [],
            "later_turn_assertions": row.get("later_turn_assertions") or [],
            "retrieval": row.get("retrieval"),
            "incident_scope": row.get("incident_scope"),
            "owner_scope": row.get("owner_scope"),
        }
        if legacy._sha_bytes(legacy._canonical(snapshot).encode("utf-8")) != \
                row.get("source_snapshot_sha256"):
            raise PostEntailmentError("source snapshot identity is invalid")
        for assertion in row.get("source_assertions") or []:
            _verify_file_binding(assertion, "bound source")
            selected = _line_slice(
                assertion["path"], assertion["line_start"], assertion["line_end"])
            if legacy._sha_bytes(selected.encode("utf-8")) != \
                    assertion.get("selected_text_sha256"):
                raise PostEntailmentError("bound source line selection changed")
        turns = candidate_replay._full_turns(row["transcript_path"])
        for assertion in row.get("later_turn_assertions") or []:
            index = assertion["assistant_turn_index"]
            if index >= len(turns) or turns[index][0] != "assistant" or \
                    legacy._sha_bytes(turns[index][1].encode("utf-8")) != \
                    assertion.get("text_sha256"):
                raise PostEntailmentError("bound later turn changed")
    return manifest


def render_inventory_report(manifest):
    totals = manifest["totals"]
    return "\n".join([
        "# M3 post-entailment Phase A inventory", "",
        "Body-free replay manifest. No claim, quote, transcript turn, or source "
        "snippet is copied here.", "",
        "- Created: `%s`" % manifest["created_at"],
        "- Policy: `%s`" % TRIAGE_POLICY,
        "- Selected terminal review candidates: **%d**" %
        totals["selected_review_candidates"],
        "- Bound source files: **%d**" % totals["bound_source_files"],
        "- Bound source assertions: **%d**" %
        totals["bound_source_assertions"],
        "- Bound later-turn assertions: **%d**" %
        totals["later_turn_assertions"],
        "- Source roles: `%s`" % legacy._canonical(
            totals["source_role_distribution"]), "",
        "## Safety", "",
        "- Provider route: `none`; provider calls are not admitted in Phase A.",
        "- Weak or unavailable retrieval cannot produce a memory candidate.",
        "- The tool has no import, deployment, or destination-write mode.",
        "- All selected upstream and stronger-source artifacts are hash-bound.", "",
    ])


def _atomicity_rule(claim):
    if _EVALUATIVE_RE.search(claim) and _NEAR_ELIMINATION_RE.search(claim):
        return "evaluative_aggregate_without_denominator"
    if _ZERO_OBSOLESCENCE_RE.search(claim):
        return "single_sample_zero_obsolescence_generalization"
    return None


def _volatility_class(claim, roles):
    if "current_project_defect" in roles:
        return "project_state"
    if roles & {"incident_observation", "incident_resolution"}:
        return "incident"
    if _CONFIG_RE.search(claim):
        return "config_route"
    if _RUNTIME_RE.search(claim):
        return "runtime_state"
    if _PROJECT_STATE_RE.search(claim):
        return "project_state"
    return "stable"


def _base_record(row, manifest_hash, when):
    return {
        "schema": RESULT_SCHEMA,
        "record_type": "result",
        "origin": "m3_post_entailment_replay",
        "triage_row_id": row["triage_row_id"],
        "candidate_id": row["candidate_id"],
        "candidate_body_sha256": row["candidate_body_sha256"],
        "candidate_manifest_sha256": row["candidate_manifest_sha256"],
        "manifest_sha256": manifest_hash,
        "transcript_sha256": row["transcript_sha256"],
        "source_snapshot_sha256": row["source_snapshot_sha256"],
        "currentness": {
            "status": "uncertain",
            "later_turn_evidence_hashes": [],
            "checked_at": when,
        },
        "atomicity": {"status": "atomic", "rule": None},
        "volatility": {"class": "stable", "expires": None},
        "novelty": {
            "status": "uncertain",
            "retrieval_mode": "unavailable",
            "no_confident_results": True,
            "related_source_ids": [],
            "relation_verdict": "not_evaluated",
        },
        "routing": {
            "destination": "manual",
            "action": "none",
            "owner_scope": row.get("owner_scope") or "manual",
            "reason": "not_evaluated",
        },
        "incident_scope": row.get("incident_scope"),
        "triage_policy": TRIAGE_POLICY,
        "timestamp": when,
        "error": None,
    }


def _finish(record, outcome, reason, error=None):
    if outcome not in ALL_OUTCOMES:
        raise PostEntailmentError("invalid post-entailment outcome")
    record["final_outcome"] = outcome
    record["final_reason"] = reason
    record["error"] = error
    return record


def _evaluate(row, result, manifest_hash, when):
    record = _base_record(row, manifest_hash, when)
    claim = str(result.get("claim") or "").strip()
    quote = str(result.get("supporting_quote") or "").strip()
    if not claim or not quote or legacy._contains_secret(
            legacy._canonical({"claim": claim, "quote": quote})):
        return _finish(record, "invalid_evidence", "secret_or_empty_candidate")
    record.update({"kind": result.get("kind"), "claim": claim,
                   "supporting_quote": quote})
    roles = {item["role"] for item in row.get("source_assertions") or []}
    volatility = _volatility_class(claim, roles)
    record["volatility"]["class"] = volatility

    explicit_superseded = [item for item in row.get("later_turn_assertions") or []
                           if item["relation"] == "supersedes"]
    ambiguous = [item for item in row.get("later_turn_assertions") or []
                 if item["relation"] == "ambiguous"]
    if explicit_superseded:
        record["currentness"].update({
            "status": "superseded",
            "later_turn_evidence_hashes": [
                item["text_sha256"] for item in explicit_superseded],
        })
        record["routing"].update({
            "destination": "reject", "action": "none",
            "reason": "later_bound_turn_supersedes_candidate",
        })
        return _finish(record, "superseded_later_context",
                       "bound_later_turn_supersedes_candidate")
    if ambiguous:
        record["currentness"]["later_turn_evidence_hashes"] = [
            item["text_sha256"] for item in ambiguous]
        record["routing"]["reason"] = "ambiguous_later_context"
        return _finish(record, "needs_manual_review", "ambiguous_later_context")

    turns = candidate_replay._full_turns(row["transcript_path"])
    _indices, later = candidate_replay._later_context(claim, quote, turns)
    if later.get("possible_correction_hits"):
        record["currentness"]["later_turn_evidence_hashes"] = [
            item["text_sha256"] for item in later["possible_correction_hits"]]
        record["routing"]["reason"] = "unresolved_later_correction_hit"
        return _finish(record, "needs_manual_review",
                       "unresolved_later_correction_hit")

    atomicity = _atomicity_rule(claim)
    if atomicity:
        record["atomicity"] = {"status": "overgeneralized", "rule": atomicity}
        record["routing"].update({
            "destination": "reject", "action": "none", "reason": atomicity,
        })
        return _finish(record, "rejected_overgeneralized", atomicity)

    source_ids = [item["source_id"]
                  for item in row.get("source_assertions") or []]
    retrieval = row.get("retrieval") or _normalize_retrieval(None)
    record["novelty"].update({
        "retrieval_mode": retrieval["mode"],
        "no_confident_results": retrieval["no_confident_results"],
        "related_source_ids": source_ids or retrieval["related_source_ids"],
    })

    if roles == {"current_project_defect"}:
        record["currentness"]["status"] = "current"
        record["novelty"]["relation_verdict"] = "destination_owned_by_current_source"
        record["routing"].update({
            "destination": "project_fix", "action": "create_candidate",
            "reason": "current_repo_owned_defect",
        })
        return _finish(record, "project_fix_candidate",
                       "bound_current_source_defect")

    incident_roles = {"incident_observation", "incident_resolution"}
    if roles == incident_roles:
        scope = row.get("incident_scope") or {}
        resolution_ids = set(scope.get("resolution_source_ids") or [])
        bound_resolution_ids = {
            item["source_id"] for item in row.get("source_assertions") or []
            if item["role"] == "incident_resolution"}
        if not scope.get("observed_at") or not scope.get("asset_scope") or \
                not resolution_ids or not resolution_ids <= bound_resolution_ids:
            record["routing"]["reason"] = "incident_scope_incomplete"
            return _finish(record, "needs_manual_review",
                           "incident_scope_incomplete")
        record["currentness"]["status"] = "current"
        record["novelty"].update({
            "status": "duplicate",
            "relation_verdict": "stronger_incident_handoff_preserves_context",
        })
        record["routing"].update({
            "destination": "incident_evidence", "action": "preserve_existing",
            "reason": "incident_observation_and_resolution_already_bound",
        })
        return _finish(record, "incident_evidence_candidate",
                       "bound_incident_with_resolution_context")
    if roles & incident_roles:
        record["routing"]["reason"] = "incident_context_incomplete"
        return _finish(record, "needs_manual_review", "incident_context_incomplete")

    if roles == {"stronger_duplicate"}:
        record["currentness"]["status"] = "current"
        record["novelty"].update({
            "status": "duplicate",
            "retrieval_mode": "hybrid" if retrieval["mode"] == "hybrid"
            else "explicit_bound_sources",
            "no_confident_results": False,
            "relation_verdict": "stronger_bound_source_owns_knowledge",
        })
        record["routing"].update({
            "destination": "reject", "action": "none",
            "reason": "stronger_bound_source_exists",
        })
        return _finish(record, "duplicate_existing",
                       "stronger_bound_source_exists")

    if roles:
        record["routing"]["reason"] = "conflicting_source_roles"
        return _finish(record, "needs_manual_review", "conflicting_source_roles")

    if volatility != "stable":
        record["routing"].update({
            "destination": "reject", "action": "none",
            "reason": "volatile_unowned_state",
        })
        return _finish(record, "rejected_volatile_state", volatility)

    # Phase A intentionally has no path to memory_candidate.  A hybrid snapshot
    # marked distinct is still only an assertion until the semantic adapter and
    # labeled organic-shadow acceptance gate exist.
    if retrieval["mode"] == "unavailable":
        reason = "semantic_retrieval_unavailable"
    elif retrieval["mode"] == "fts_only" or \
            retrieval["no_confident_results"]:
        reason = "weak_retrieval_not_novelty_evidence"
    else:
        reason = "phase_a_semantic_novelty_not_admitted"
    record["novelty"]["relation_verdict"] = reason
    record["routing"]["reason"] = reason
    return _finish(record, "needs_manual_review", reason)


def _bind_output(output_dir, manifest_hash, when):
    path = os.path.join(output_dir, "triage-manifest-binding.json")
    artifacts = tuple(os.path.join(output_dir, name) for name in (
        "triage-results.jsonl", "triage-checkpoint.json", "triage-summary.json"))
    if not os.path.lexists(path):
        if any(os.path.lexists(item) for item in artifacts):
            raise PostEntailmentError(
                "existing triage output lacks immutable manifest binding")
        legacy._atomic_json(path, {
            "schema": BINDING_SCHEMA,
            "created_at": when,
            "manifest_sha256": manifest_hash,
        })
    else:
        binding = _load_json(path, "triage manifest binding")
        if binding.get("schema") != BINDING_SCHEMA or \
                binding.get("manifest_sha256") != manifest_hash:
            raise PostEntailmentError(
                "triage output is bound to a different manifest")


def _prepare_append(path, manifest_hash, when):
    legacy._reject_symlink(path)
    if not os.path.lexists(path) or os.path.getsize(path) == 0:
        return False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        complete = fh.read(1) == b"\n"
    if complete:
        return False
    legacy._append_bytes(path, "\n")
    legacy._append_record(path, {
        "schema": RESULT_SCHEMA,
        "record_type": "recovery_marker",
        "manifest_sha256": manifest_hash,
        "timestamp": when,
        "reason": "truncated_final_append_preserved",
    })
    return True


def _latest_results(path, manifest_hash):
    legacy._reject_symlink(path)
    rows, malformed = legacy._read_jsonl(path)
    latest, terminals = {}, {}
    for row in rows:
        if row.get("record_type") in ("result", "recovery_marker") and \
                row.get("manifest_sha256") != manifest_hash:
            raise PostEntailmentError("triage result manifest binding changed")
        if row.get("record_type") != "result" or not row.get("triage_row_id"):
            continue
        rid = row["triage_row_id"]
        latest[rid] = row
        if row.get("final_outcome") in TERMINAL_OUTCOMES:
            terminals[rid] = row
    return latest, terminals, malformed


def _verify_terminal(row, result, manifest_hash):
    if result.get("schema") != RESULT_SCHEMA or \
            result.get("manifest_sha256") != manifest_hash or \
            result.get("candidate_id") != row.get("candidate_id") or \
            result.get("candidate_body_sha256") != \
            row.get("candidate_body_sha256") or \
            result.get("source_snapshot_sha256") != \
            row.get("source_snapshot_sha256") or \
            result.get("triage_policy") != TRIAGE_POLICY:
        raise PostEntailmentError("terminal triage result binding differs")


def _summary(selected, results_path, manifest_hash, when):
    latest, terminals, malformed = _latest_results(results_path, manifest_hash)
    ids = [row["triage_row_id"] for row in selected]
    terminal = {rid: terminals[rid] for rid in ids if rid in terminals}
    retries = {rid: latest[rid] for rid in ids if rid not in terminal and
               latest.get(rid, {}).get("final_outcome") == "transient_retry"}
    pending = [rid for rid in ids if rid not in terminal and rid not in retries]
    outcomes = Counter(row["final_outcome"] for row in terminal.values())
    reasons = Counter(row.get("final_reason") or "<missing>"
                      for row in list(terminal.values()) + list(retries.values()))
    return {
        "schema": SUMMARY_SCHEMA,
        "generated_at": when,
        "selected_input_count": len(ids),
        "terminal_count": len(terminal),
        "nonterminal_retry_count": len(retries),
        "pending_count": len(pending),
        "terminal_outcomes": dict(sorted(outcomes.items())),
        "reason_distribution": dict(sorted(reasons.items())),
        "memory_candidate_count": outcomes.get("memory_candidate", 0),
        "malformed_result_lines_preserved": len(malformed),
        "reconciled": len(ids) == len(terminal) + len(retries) + len(pending),
        "provider_calls": 0,
        "production_writes": 0,
    }


def run_replay(manifest, output_dir, offset=0, limit=None, workers=1):
    if int(workers) != MAX_WORKERS:
        raise PostEntailmentError("only workers=1 is admitted in Phase A")
    output_dir = os.path.realpath(output_dir)
    roots = legacy._canonical_production_roots(manifest["projects_root"])
    legacy._guard_output(output_dir, manifest["memory_system"],
                         manifest["projects_root"], roots)
    source_dir = os.path.dirname(manifest["candidate_results"]["path"])
    legacy._guard_distinct_output(output_dir, protected_dirs=(source_dir,))
    os.makedirs(output_dir, mode=0o700, exist_ok=True)
    with legacy._output_lock(output_dir):
        manifest_hash = legacy._manifest_hash(manifest)
        when = manifest["created_at"]
        _bind_output(output_dir, manifest_hash, when)
        results_path = os.path.join(output_dir, "triage-results.jsonl")
        checkpoint_path = os.path.join(output_dir, "triage-checkpoint.json")
        summary_path = os.path.join(output_dir, "triage-summary.json")
        for path in (results_path, checkpoint_path, summary_path):
            legacy._reject_symlink(path)
        _prepare_append(results_path, manifest_hash, when)

        selected = manifest["rows"][max(0, offset):]
        if limit is not None:
            selected = selected[:max(0, limit)]

        # Revalidate all immutable inputs before persisting the first result.
        inventory, candidate_manifest_hash, upstream = _load_upstream(
            manifest["candidate_inventory"]["path"],
            manifest["candidate_results"]["path"])
        meta_by_id = {row["candidate_id"]: row for row in inventory["rows"]}
        loaded = []
        for row in selected:
            meta = meta_by_id.get(row["candidate_id"])
            result = upstream.get(row["candidate_id"])
            if not meta or not result:
                raise PostEntailmentError("selected upstream candidate disappeared")
            _strict_upstream(meta, result, candidate_manifest_hash)
            loaded.append((row, result))

        latest, terminals, _malformed = _latest_results(
            results_path, manifest_hash)
        for row, _result in loaded:
            if row["triage_row_id"] in terminals:
                _verify_terminal(row, terminals[row["triage_row_id"]], manifest_hash)
        for row, result in loaded:
            if row["triage_row_id"] in terminals:
                continue
            record = _evaluate(row, result, manifest_hash, when)
            # append+fsync per unit: a stop can only replay unfinished rows.
            legacy._append_record(results_path, record)

        summary = _summary(selected, results_path, manifest_hash, when)
        latest, terminals, malformed = _latest_results(results_path, manifest_hash)
        ids = {row["triage_row_id"] for row in selected}
        checkpoint = {
            "schema": CHECKPOINT_SCHEMA,
            "updated_at": when,
            "manifest_sha256": manifest_hash,
            "selected_offset": offset,
            "selected_limit": limit,
            "workers": int(workers),
            "provider_calls_admitted": False,
            "completed_triage_row_ids": sorted(rid for rid in terminals if rid in ids),
            "retry_triage_row_ids": sorted(
                rid for rid, item in latest.items() if rid in ids and
                rid not in terminals and item.get("final_outcome") ==
                "transient_retry"),
            "malformed_result_lines_preserved": len(malformed),
        }
        legacy._atomic_json(summary_path, summary)
        legacy._atomic_json(checkpoint_path, checkpoint)
        return summary, results_path


def render_review_packet(manifest, results_path):
    manifest_hash = legacy._manifest_hash(manifest)
    latest, terminals, malformed = _latest_results(results_path, manifest_hash)
    rows_by_id = {row["triage_row_id"]: row for row in manifest["rows"]}
    terminal_rows = []
    retries = []
    for row in manifest["rows"]:
        rid = row["triage_row_id"]
        if rid in terminals:
            _verify_terminal(row, terminals[rid], manifest_hash)
            terminal_rows.append(terminals[rid])
        elif latest.get(rid, {}).get("final_outcome") == "transient_retry":
            retries.append(latest[rid])
    pending = len(manifest["rows"]) - len(terminal_rows) - len(retries)
    outcomes = Counter(row["final_outcome"] for row in terminal_rows)
    grouped = defaultdict(list)
    for row in terminal_rows:
        grouped[(row.get("routing") or {}).get("destination", "manual")].append(row)
    lines = [
        "# M3 post-entailment Phase A review packet", "",
        "Replay-only. No memory import, project mutation, incident mutation, "
        "provider call, or D5 write has occurred.", "",
        "## Accounting", "",
        "- Inputs: **%d**" % len(manifest["rows"]),
        "- Terminal: **%d**" % len(terminal_rows),
        "- Retries: **%d**" % len(retries),
        "- Pending: **%d**" % pending,
        "- Reconciled: **%s**" % str(
            len(manifest["rows"]) == len(terminal_rows) + len(retries) + pending
        ).lower(),
        "- Malformed lines preserved: **%d**" % len(malformed),
        "- Outcomes: `%s`" % legacy._canonical(dict(sorted(outcomes.items()))),
        "- Memory candidates: **%d**" % outcomes.get("memory_candidate", 0), "",
    ]
    order = ("project_fix", "incident_evidence", "memory", "reject", "manual")
    for destination in order:
        items = grouped.get(destination) or []
        if not items:
            continue
        lines += ["## Destination: `%s`" % destination, ""]
        for index, item in enumerate(items, 1):
            triage_meta = rows_by_id[item["triage_row_id"]]
            pointers = ["`%s:%s`" % (
                source["path"], source["line_start"])
                for source in triage_meta.get("source_assertions") or []]
            lines += [
                "### %d. `%s` — `%s`" % (
                    index, item["candidate_id"][:12], item["final_outcome"]), "",
                "Claim: %s" % legacy._review_claim(item.get("claim")), "",
                "Exact supporting quote (JSON string): `%s`" %
                legacy._canonical(item.get("supporting_quote") or ""), "",
                "Currentness: `%s`; volatility: `%s`; novelty: `%s`." % (
                    item["currentness"]["status"], item["volatility"]["class"],
                    item["novelty"]["status"]), "",
                "Owner/action: `%s` / `%s`." % (
                    item["routing"]["owner_scope"], item["routing"]["action"]), "",
                "Incident scope: `%s`." % legacy._canonical(
                    item.get("incident_scope")) if item.get("incident_scope") else
                "Incident scope: none.", "",
                "Bound source pointers: %s" % (", ".join(pointers) or "none"), "",
                "Review: [ ] accept  [ ] reject  [ ] rewrite as new candidate  "
                "[ ] reroute", "",
            ]
    lines += [
        "## Stop condition", "",
        "There is intentionally no action or command in this packet that imports "
        "a memory or mutates an external destination.", "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("inventory")
    inv.add_argument("--candidate-inventory", required=True)
    inv.add_argument("--candidate-results", required=True)
    inv.add_argument("--source-catalog", required=True)
    inv.add_argument("--output-dir", required=True)

    replay = sub.add_parser("replay")
    replay.add_argument("--manifest", required=True)
    replay.add_argument("--output-dir", required=True)
    replay.add_argument("--offset", type=int, default=0)
    replay.add_argument("--limit", type=int, default=None)
    replay.add_argument("--workers", type=int, default=1)

    review = sub.add_parser("review")
    review.add_argument("--manifest", required=True)
    review.add_argument("--results", required=True)
    review.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if args.command == "inventory":
        inventory, _hash, _results = _load_upstream(
            args.candidate_inventory, args.candidate_results)
        roots = legacy._canonical_production_roots(inventory["projects_root"])
        legacy._guard_output(args.output_dir, inventory["memory_system"],
                             inventory["projects_root"], roots)
        source_dir = os.path.dirname(os.path.realpath(args.candidate_results))
        legacy._guard_distinct_output(args.output_dir, protected_dirs=(source_dir,))
        os.makedirs(args.output_dir, mode=0o700, exist_ok=True)
        manifest_path = os.path.join(args.output_dir, "triage-manifest.json")
        report_path = os.path.join(args.output_dir, "triage-inventory-report.md")
        manifest = build_manifest(
            args.candidate_inventory, args.candidate_results, args.source_catalog)
        legacy._atomic_json(manifest_path, manifest)
        legacy._write_text(report_path, render_inventory_report(manifest))
        print(legacy._canonical({
            "status": "ok", "phase": "A", "replay_only": True,
            "provider_calls": 0, "production_write": False,
            "manifest": manifest_path, "totals": manifest["totals"],
        }))
        return 0

    if args.command == "replay":
        manifest = _load_manifest(args.manifest)
        summary, results = run_replay(
            manifest, args.output_dir, offset=args.offset, limit=args.limit,
            workers=args.workers)
        print(legacy._canonical({
            "status": "ok", "phase": "A", "replay_only": True,
            "provider_calls": 0, "production_write": False,
            "results": results, "summary": summary,
        }))
        return 0

    manifest = _load_manifest(args.manifest, require_current_policy=False)
    roots = legacy._canonical_production_roots(manifest["projects_root"])
    legacy._guard_output(args.output, manifest["memory_system"],
                         manifest["projects_root"], roots)
    legacy._guard_distinct_output(args.output, (
        args.manifest, args.results,
        os.path.join(os.path.dirname(args.results), "triage-manifest-binding.json"),
        os.path.join(os.path.dirname(args.results), "triage-checkpoint.json"),
        os.path.join(os.path.dirname(args.results), "triage-summary.json"),
    ))
    packet = render_review_packet(manifest, args.results)
    legacy._write_text(args.output, packet)
    print(legacy._canonical({
        "status": "ok", "phase": "A", "replay_only": True,
        "provider_calls": 0, "production_write": False,
        "output": args.output,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
