from __future__ import annotations

import sys
from pathlib import Path

import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import m3_judge_shimnachi_worker as worker  # noqa: E402
from m3_judge_core import make_request  # noqa: E402


BASE_ARGS = [
    "--model-id",
    "candidate",
    "--model-revision",
    "revision",
    "--artifact-sha256",
    "a" * 64,
    "--quantization",
    "Q4",
    "--runtime-version",
    "runtime",
]



class ShimnachiWorkerTest(unittest.TestCase):
    def test_benchmark_admission_state_remains_default(self) -> None:
        args = worker.parse_args(BASE_ARGS)
        assert worker.provenance(args)["admission_state"] == "benchmark_only"


    def test_production_admission_state_is_explicit(self) -> None:
        args = worker.parse_args(
            [
                *BASE_ARGS,
                "--deployment-fingerprint",
                "b" * 64,
                "--admission-state",
                "admitted",
            ]
        )
        route = worker.provenance(args)
        assert route["admission_state"] == "admitted"
        assert route["deployment_fingerprint"] == "b" * 64
        assert route["cloud_fallback"] is False


    def test_production_requires_deployment_fingerprint(self) -> None:
        with self.assertRaises(SystemExit):
            worker.parse_args([*BASE_ARGS, "--admission-state", "admitted"])


    def test_worker_fails_closed_on_response_deployment_mismatch(self) -> None:
        args = worker.parse_args(
            [
                *BASE_ARGS,
                "--deployment-fingerprint",
                "b" * 64,
                "--admission-state",
                "admitted",
            ]
        )
        request = make_request(
            request_id="fixture",
            role="entailment",
            routing_mode="local_default",
            prompt_version=args.prompt_profile,
            payload={
                "claim": "The retry limit is four.",
                "spans": ["The service retries each failed request at most four times."],
            },
            deterministic_rail_passed=True,
        )
        patcher = mock.patch.object(
            worker,
            "_post_json",
            lambda *_args, **_kwargs: {
                "shimnachi": {"deployment_fingerprint": "c" * 64},
                "choices": [
                    {
                        "message": {
                            "content": '{"entailed":true,"quote":"The service retries each failed request at most four times."}'
                        }
                    }
                ],
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        result = worker.evaluate(request, args, worker.provenance(args), token="fixture")
        assert result["status"] == "malformed"
        assert result["evidence"]["error_code"] == "invalid_gateway_response"
