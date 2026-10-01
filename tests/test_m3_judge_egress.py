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


class JudgeEgressTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSDK()
        self._orig = m3_judge._get_sdk
        m3_judge._get_sdk = lambda: self.fake

    def tearDown(self):
        m3_judge._get_sdk = self._orig

    def test_only_redacted_text_is_sent_and_gate_still_passes(self):
        claim = "The deploy key owner is ops@example.com (token sk-ant-api03-abcdefghijklmnopqrstuvwx)."
        spans = ["the deploy key lives at ops@example.com on the build host 100.104.99.58"]
        status = m3_judge.verdict(claim, spans)
        sent = "\n".join(self.fake.users)
        for leaked in ("ops@example.com", "sk-ant-api03", "100.104.99.58"):
            self.assertNotIn(leaked, sent)
        self.assertIn("[REDACTED-EMAIL]", sent)
        self.assertEqual(status, "entailed")


if __name__ == "__main__":
    unittest.main()
