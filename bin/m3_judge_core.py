#!/usr/bin/env python3
"""Provider-independent contracts for Eidetic Core M3 judges.

This module has no provider SDK imports and performs no I/O.  It owns the
role-specific envelopes and deterministic post-validation used by local and
external adapters.  A model response is never authoritative until it passes
``validate_result``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence


SCHEMA_VERSION = "m3-judge-result/v1"
REQUEST_SCHEMA_VERSION = "m3-judge-request/v1"

ROLES = (
    "durability",
    "entailment",
    "relation",
    "commission",
    "arbiter",
)
ROUTING_MODES = (
    "offline_strict",
    "local_default",
    "external_reference",
    "dark_compare",
)
STATUSES = (
    "ok",
    "abstain",
    "unavailable",
    "malformed",
    "timeout",
    "oom",
    "crash",
    "policy_reject",
)

ROLE_LABELS = {
    "durability": {"durable", "transient", "abstain"},
    "entailment": {"entailed", "not_entailed", "abstain"},
    "relation": {
        "duplicate", "contradiction", "supersedes", "unrelated", "abstain"
    },
    "commission": {"keep", "noise", "dangerous_wrong", "abstain"},
    "arbiter": {"resolved", "unresolved", "abstain"},
}

ROLE_MAX_CONTEXT_CHARS = {
    "durability": 12_000,
    "entailment": 32_000,
    "relation": 32_000,
    "commission": 32_000,
    "arbiter": 48_000,
}

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class ContractError(ValueError):
    """The request or result violates a frozen judge contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Return the first balanced JSON object, tolerating prose wrappers."""
    text = (text or "").strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (TypeError, json.JSONDecodeError):
        pass
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        quoted = False
        escaped = False
        for end in range(start, len(text)):
            char = text[end]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
                    return obj if isinstance(obj, dict) else None
    return None


def quote_is_verbatim(quote: str, spans: Sequence[str], min_words: int = 6) -> bool:
    """Strict v1 quote gate: literal substring in one span, with word floor."""
    if not isinstance(quote, str) or len(_WORD_RE.findall(quote)) < min_words:
        return False
    return any(quote in span for span in spans if isinstance(span, str))


def _text_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_text_size(k) + _text_size(v) for k, v in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return sum(_text_size(item) for item in value)
    return 0


def validate_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    required = {
        "schema_version", "request_id", "input_hash", "role", "routing_mode",
        "prompt_version", "payload", "deterministic_rail_passed",
    }
    missing = required - set(request)
    if missing:
        raise ContractError("missing request fields: " + ", ".join(sorted(missing)))
    if request["schema_version"] != REQUEST_SCHEMA_VERSION:
        raise ContractError("unsupported request schema")
    role = request["role"]
    if role not in ROLES:
        raise ContractError("unsupported judge role")
    if request["routing_mode"] not in ROUTING_MODES:
        raise ContractError("unsupported routing mode")
    if not isinstance(request["payload"], dict):
        raise ContractError("payload must be an object")
    if request["input_hash"] != content_hash(request["payload"]):
        raise ContractError("input_hash does not match payload")
    if not isinstance(request["deterministic_rail_passed"], bool):
        raise ContractError("deterministic_rail_passed must be boolean")
    if _text_size(request["payload"]) > ROLE_MAX_CONTEXT_CHARS[role]:
        raise ContractError("role context limit exceeded")
    payload = request["payload"]
    if role == "durability":
        if not isinstance(payload.get("candidate"), str):
            raise ContractError("durability candidate must be text")
    elif role == "entailment":
        if not isinstance(payload.get("claim"), str):
            raise ContractError("entailment claim must be text")
        spans = payload.get("spans")
        if not isinstance(spans, list) or not all(isinstance(x, str) for x in spans):
            raise ContractError("entailment spans must be a text array")
    elif role == "relation":
        existing = payload.get("existing")
        if not isinstance(payload.get("candidate"), str):
            raise ContractError("relation candidate must be text")
        if not isinstance(existing, list) or not all(isinstance(x, str) for x in existing):
            raise ContractError("relation existing must be a text array")
        source = payload.get("source_evidence")
        if source is not None and (not isinstance(source, list) or
                                   not all(isinstance(x, str) for x in source)):
            raise ContractError("relation source_evidence must be a text array when supplied")
    elif role == "commission":
        source = payload.get("source_evidence")
        if not isinstance(payload.get("candidate"), str):
            raise ContractError("commission candidate must be text")
        if not isinstance(source, list) or not all(isinstance(x, str) for x in source):
            raise ContractError("commission source_evidence must be a text array")
        if not isinstance(payload.get("policy_version"), str):
            raise ContractError("commission policy_version must be text")
    elif role == "arbiter":
        if not isinstance(payload.get("request"), dict):
            raise ContractError("arbiter request must be an object")
        if not isinstance(payload.get("individual_results"), list):
            raise ContractError("arbiter individual_results must be an array")
        if len(payload["individual_results"]) < 2:
            raise ContractError("arbiter requires at least two individual results")
        if not isinstance(payload.get("policy_version"), str):
            raise ContractError("arbiter policy_version must be text")
    return dict(request)


def make_request(*, request_id: str, role: str, routing_mode: str,
                 prompt_version: str, payload: Dict[str, Any],
                 deterministic_rail_passed: bool) -> Dict[str, Any]:
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "role": role,
        "routing_mode": routing_mode,
        "prompt_version": prompt_version,
        "payload": payload,
        "deterministic_rail_passed": deterministic_rail_passed,
    }
    request["input_hash"] = content_hash(payload)
    return validate_request(request)


def _validate_provenance(provenance: Any) -> None:
    if not isinstance(provenance, dict):
        raise ContractError("provenance must be an object")
    required = {
        "backend", "runtime", "runtime_version", "model_id", "artifact_hash",
        "quantization", "prompt_version", "schema_version", "decoding",
    }
    missing = required - set(provenance)
    if missing:
        raise ContractError("missing provenance fields: " + ", ".join(sorted(missing)))


def validate_result(request: Mapping[str, Any], result: Mapping[str, Any]) -> Dict[str, Any]:
    """Fail-closed deterministic validation of a provider-independent result."""
    request = validate_request(request)
    required = {
        "schema_version", "request_id", "role", "status", "label",
        "confidence", "evidence", "provenance", "latency_ms", "retryable",
    }
    missing = required - set(result)
    if missing:
        raise ContractError("missing result fields: " + ", ".join(sorted(missing)))
    if result["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported result schema")
    if result["request_id"] != request["request_id"] or result["role"] != request["role"]:
        raise ContractError("result does not match request")
    if result["status"] not in STATUSES:
        raise ContractError("unsupported result status")
    if result["label"] not in ROLE_LABELS[request["role"]]:
        raise ContractError("unsupported role label")
    confidence = result["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ContractError("confidence must be in [0, 1]")
    if not isinstance(result["evidence"], dict):
        raise ContractError("evidence must be an object")
    if not isinstance(result["latency_ms"], (int, float)) or result["latency_ms"] < 0:
        raise ContractError("latency_ms must be non-negative")
    if not isinstance(result["retryable"], bool):
        raise ContractError("retryable must be boolean")
    _validate_provenance(result["provenance"])

    # Any runtime or policy failure has no accepting authority.
    if result["status"] != "ok" and result["label"] != "abstain":
        raise ContractError("non-ok result must abstain")

    if request["role"] == "entailment" and result["label"] == "entailed":
        quote = result["evidence"].get("quote")
        spans = request["payload"].get("spans", [])
        if not quote_is_verbatim(quote, spans):
            raise ContractError("entailed result lacks a verbatim quote")
    if request["role"] == "relation" and result["label"] not in {"unrelated", "abstain"}:
        index = result["evidence"].get("existing_index")
        if not isinstance(index, int) or not 0 <= index < len(request["payload"]["existing"]):
            raise ContractError("relation result lacks a valid existing_index")
    if request["role"] == "commission" and result["label"] == "dangerous_wrong":
        ref = result["evidence"].get("evidence_ref")
        if not isinstance(ref, str) or not ref.strip():
            raise ContractError("dangerous_wrong requires evidence_ref")
    if request["role"] == "arbiter" and result["label"] == "resolved":
        selected = result["evidence"].get("selected_result_hash")
        if not isinstance(selected, str) or not selected:
            raise ContractError("resolved arbiter result requires selected_result_hash")
    return dict(result)


def fail_closed_result(request: Mapping[str, Any], *, status: str,
                       provenance: Mapping[str, Any], latency_ms: float = 0,
                       retryable: bool = False,
                       error_code: Optional[str] = None) -> Dict[str, Any]:
    if status not in STATUSES or status == "ok":
        raise ContractError("fail_closed_result requires a non-ok status")
    result = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request["request_id"],
        "role": request["role"],
        "status": status,
        "label": "abstain",
        "confidence": 0.0,
        "evidence": {"error_code": error_code or status},
        "provenance": dict(provenance),
        "latency_ms": float(latency_ms),
        "retryable": bool(retryable),
    }
    return validate_result(request, result)


@dataclass(frozen=True)
class BackendCapability:
    backend: str
    roles: tuple[str, ...]
    local: bool
    network_required: bool
    max_context_chars: int
