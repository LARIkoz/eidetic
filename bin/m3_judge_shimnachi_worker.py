#!/usr/bin/env python3
"""Persistent Eidetic M3 entailment worker for the shimnachi local-only route.

The worker speaks the existing one-request/one-result NDJSON protocol. It may
connect only to a loopback HTTP endpoint, expected to be an SSH tunnel to the
private shimnachi gateway. No provider or cloud fallback exists here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import m3_judge  # noqa: E402
import m3_judge_mlx_worker as prompt_contract  # noqa: E402
from m3_judge_core import (  # noqa: E402
    SCHEMA_VERSION,
    ContractError,
    fail_closed_result,
    parse_first_json_object,
    quote_is_verbatim,
    validate_request,
    validate_result,
)


OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entailed", "quote"],
    "properties": {
        "entailed": {"type": "boolean"},
        "quote": {"type": "string"},
    },
}


def _sha256_arg(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise argparse.ArgumentTypeError("artifact hash must be lowercase SHA-256")
    return value


def _loopback_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise argparse.ArgumentTypeError("base URL must be loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("base URL must not contain credentials or query data")
    return value.rstrip("/")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        type=_loopback_url,
        default=os.getenv("SHIMNACHI_LOCAL_URL", "http://127.0.0.1:18080"),
    )
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--artifact-sha256", required=True, type=_sha256_arg)
    parser.add_argument("--deployment-fingerprint", type=_sha256_arg)
    parser.add_argument("--quantization", required=True)
    parser.add_argument("--runtime-version", required=True)
    parser.add_argument(
        "--admission-state",
        choices=("benchmark_only", "admitted"),
        default="benchmark_only",
        help="Exact gateway admission state recorded in result provenance.",
    )
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--reasoning-budget-tokens", type=int, default=128)
    parser.add_argument(
        "--prompt-profile",
        default=prompt_contract.PROMPT_V6_VERSION,
        choices=tuple(prompt_contract.PROMPT_PROFILES),
    )
    parser.add_argument("--print-provenance", action="store_true")
    args = parser.parse_args(argv)
    if args.max_tokens < 32:
        parser.error("--max-tokens must be at least 32")
    if args.reasoning_budget_tokens < 0:
        parser.error("--reasoning-budget-tokens cannot be negative")
    if args.admission_state == "admitted" and not args.deployment_fingerprint:
        parser.error("--deployment-fingerprint is required for admitted routes")
    return args


def provenance(args: argparse.Namespace) -> Dict[str, Any]:
    result = {
        "backend": "shimnachi-local",
        "runtime": "llama.cpp-http",
        "runtime_version": args.runtime_version,
        "model_id": args.model_id,
        "model_revision": args.model_revision,
        "artifact_hash": args.artifact_sha256,
        "quantization": args.quantization,
        "prompt_version": args.prompt_profile,
        "prompt_sha256": prompt_contract.prompt_sha256(args.prompt_profile),
        "prompt_contract_sha256": prompt_contract.prompt_contract_sha256(
            args.prompt_profile
        ),
        "schema_version": SCHEMA_VERSION,
        "quote_gate": "literal_verbatim_one_span_min_6_words/v1",
        "route_id": "shimnachi/local",
        "transport": "private-loopback-ssh-tunnel",
        "admission_state": args.admission_state,
        "cloud_fallback": False,
        "decoding": {
            "temperature": 0.0,
            "max_tokens": args.max_tokens,
            "seed": 1,
            "reasoning_budget_tokens": args.reasoning_budget_tokens,
            "mtp": False,
            "target_route_fallback": False,
        },
    }
    if args.deployment_fingerprint:
        result["deployment_fingerprint"] = args.deployment_fingerprint
    return result


def _messages(request: Mapping[str, Any], prompt_profile: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": prompt_contract.prompt_text(prompt_profile)},
        {
            "role": "user",
            "content": (
                m3_judge._build_user(
                    request["payload"]["claim"], request["payload"]["spans"]
                )
                + prompt_contract.PROMPT_USER_SUFFIXES[prompt_profile]
            ),
        },
    ]


def _post_json(url: str, token: str, payload: Mapping[str, Any], timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        parsed = json.load(response)
    if not isinstance(parsed, dict):
        raise ValueError("gateway returned non-object JSON")
    return parsed


def _failure(
    request: Mapping[str, Any],
    route_provenance: Mapping[str, Any],
    *,
    status: str,
    code: str,
    latency_ms: float,
    retryable: bool,
) -> dict:
    return fail_closed_result(
        request,
        status=status,
        provenance=route_provenance,
        error_code=code,
        latency_ms=latency_ms,
        retryable=retryable,
    )


def evaluate(
    request: Mapping[str, Any],
    args: argparse.Namespace,
    route_provenance: Mapping[str, Any],
    *,
    token: str,
) -> dict:
    started = time.monotonic()
    request = validate_request(request)
    if request["role"] != "entailment":
        return _failure(
            request,
            route_provenance,
            status="unavailable",
            code="role_not_supported",
            latency_ms=0.0,
            retryable=False,
        )
    if request["routing_mode"] != "local_default":
        return _failure(
            request,
            route_provenance,
            status="policy_reject",
            code="shimnachi_requires_local_default",
            latency_ms=0.0,
            retryable=False,
        )
    if request["prompt_version"] != args.prompt_profile:
        return _failure(
            request,
            route_provenance,
            status="policy_reject",
            code="prompt_version_mismatch",
            latency_ms=0.0,
            retryable=False,
        )

    payload = {
        "model": "shimnachi/local",
        "messages": _messages(request, args.prompt_profile),
        "temperature": 0.0,
        "max_tokens": args.max_tokens,
        "seed": 1,
        "reasoning_budget_tokens": args.reasoning_budget_tokens,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "eidetic_m3_entailment",
                "strict": True,
                "schema": OUTPUT_SCHEMA,
            },
        },
    }
    try:
        response = _post_json(
            f"{args.base_url}/v1/chat/completions",
            token,
            payload,
            timeout=240.0,
        )
        if args.deployment_fingerprint and (
            response.get("shimnachi", {}).get("deployment_fingerprint")
            != args.deployment_fingerprint
        ):
            raise ValueError("deployment_fingerprint_mismatch")
        text = response["choices"][0]["message"]["content"]
        parsed = parse_first_json_object(text)
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - started) * 1000
        return _failure(
            request,
            route_provenance,
            status="unavailable",
            code=f"gateway_http_{exc.code}",
            latency_ms=elapsed,
            retryable=exc.code >= 500,
        )
    except (TimeoutError, urllib.error.URLError):
        elapsed = (time.monotonic() - started) * 1000
        return _failure(
            request,
            route_provenance,
            status="timeout",
            code="gateway_timeout",
            latency_ms=elapsed,
            retryable=True,
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        elapsed = (time.monotonic() - started) * 1000
        return _failure(
            request,
            route_provenance,
            status="malformed",
            code="invalid_gateway_response",
            latency_ms=elapsed,
            retryable=False,
        )

    elapsed = (time.monotonic() - started) * 1000
    if parsed is None or not isinstance(parsed.get("entailed"), bool):
        return _failure(
            request,
            route_provenance,
            status="malformed",
            code="invalid_json_verdict",
            latency_ms=elapsed,
            retryable=False,
        )
    entailed = parsed["entailed"]
    quote = str(parsed.get("quote") or "")
    if entailed and not quote_is_verbatim(quote, request["payload"]["spans"]):
        return _failure(
            request,
            route_provenance,
            status="malformed",
            code="unverifiable_quote",
            latency_ms=elapsed,
            retryable=False,
        )
    result = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request["request_id"],
        "role": "entailment",
        "status": "ok",
        "label": "entailed" if entailed else "not_entailed",
        "confidence": 1.0,
        "evidence": {"quote": quote if entailed else ""},
        "provenance": dict(route_provenance),
        "latency_ms": elapsed,
        "retryable": False,
    }
    return validate_result(request, result)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    route_provenance = provenance(args)
    if args.print_provenance:
        print(json.dumps(route_provenance, ensure_ascii=False, sort_keys=True))
        return 0
    token = os.getenv("SHIMNACHI_LOCAL_TOKEN", "").strip()
    if not token:
        raise SystemExit("SHIMNACHI_LOCAL_TOKEN is required")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            result = evaluate(request, args, route_provenance, token=token)
        except (json.JSONDecodeError, ContractError, KeyError, TypeError, ValueError):
            print(json.dumps({"protocol_error": "invalid_request"}), flush=True)
            continue
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
