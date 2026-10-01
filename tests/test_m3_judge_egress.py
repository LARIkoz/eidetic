import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import m3_judge  # noqa: E402


class FakeSDK:
    def __init__(self):
        self.users = []

    def chat_for_route(self, **kw):
        self.users.append(kw["user"])
        quote = "deploy key lives at [REDACTED-EMAIL] on the build host"
        return {"content": json.dumps({"entailed": True, "quote": quote}),
                "response_shape": {"ok": True}}


class ExternalJudgeEgressTest(unittest.TestCase):
    """An external judge (any provider but shimnachi) only ever sees redacted text."""

    def setUp(self):
        self.fake = FakeSDK()
        self._orig = (m3_judge._get_sdk, getattr(m3_judge, "_LOCAL", False))
        m3_judge._get_sdk = lambda: self.fake
        m3_judge._LOCAL = False

    def tearDown(self):
        m3_judge._get_sdk, m3_judge._LOCAL = self._orig

    def test_only_redacted_text_is_sent_and_gate_still_passes(self):
        claim = "The deploy key owner is ops@example.com (token sk-ant-api03-abcdefghijklmnopqrstuvwx)."
        spans = ["the deploy key lives at ops@example.com on the build host 100.104.99.58"]
        status = m3_judge.verdict(claim, spans)
        sent = "\n".join(self.fake.users)
        for leaked in ("ops@example.com", "sk-ant-api03", "100.104.99.58"):
            self.assertNotIn(leaked, sent)
        self.assertIn("[REDACTED-EMAIL]", sent)
        self.assertEqual(status, "entailed")


class LocalJudgeRequestTest(unittest.TestCase):
    """The local lane sends the AC-0-measured request and keeps the literal quote gate."""

    def setUp(self):
        import m3_judge_shimnachi_worker as worker
        self.worker = worker
        self.sent = []
        self._orig = (worker._post_json, m3_judge._LOCAL, os.environ.get("SHIMNACHI_LOCAL_TOKEN"))
        m3_judge._LOCAL = True
        os.environ["SHIMNACHI_LOCAL_TOKEN"] = "test-token"
        self.reply = {"entailed": True, "quote": "the amber worker stops after three consecutive failures"}

        def fake_post(url, token, payload, timeout):
            self.sent.append(payload)
            return {"choices": [{"message": {"content": json.dumps(self.reply)}}]}
        worker._post_json = fake_post

    def tearDown(self):
        self.worker._post_json, m3_judge._LOCAL, token = self._orig
        if token is None:
            os.environ.pop("SHIMNACHI_LOCAL_TOKEN", None)
        else:
            os.environ["SHIMNACHI_LOCAL_TOKEN"] = token

    def test_request_matches_the_measured_contract(self):
        spans = ["Log: the amber worker stops after three consecutive failures and pages ops@example.com."]
        self.assertEqual(m3_judge.verdict("The amber worker stops after three failures.", spans), "entailed")
        payload = self.sent[0]
        self.assertEqual(payload["model"], "shimnachi/local")
        self.assertEqual(payload["messages"][0]["content"],
                         self.worker.prompt_contract.prompt_text("m3-entailment-local-v6"))
        self.assertEqual((payload["temperature"], payload["max_tokens"], payload["seed"],
                          payload["reasoning_budget_tokens"]), (0.0, 400, 1, 128))
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], self.worker.OUTPUT_SCHEMA)
        # Local text is not redacted: the judge sees what AC-0 saw.
        self.assertIn("ops@example.com", payload["messages"][1]["content"])

    def test_literal_gate_rejects_a_paraphrased_quote(self):
        self.reply = {"entailed": True, "quote": "The Amber worker stops after three consecutive failures"}
        spans = ["the amber worker stops after three consecutive failures and pages the operator"]
        self.assertEqual(m3_judge.verdict("The amber worker stops after three failures.", spans),
                         "not_entailed")


if __name__ == "__main__":
    unittest.main()
