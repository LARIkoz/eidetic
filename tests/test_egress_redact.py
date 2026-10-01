import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import egress_redact  # noqa: E402


class RedactTest(unittest.TestCase):
    def check(self, text, gone, label):
        out = egress_redact.redact(text)
        self.assertNotIn(gone, out)
        self.assertIn(f"[REDACTED-{label}]", out)

    def test_secrets(self):
        self.check("key sk-ant-api03-abcdefghijklmnopqrstuvwx here", "abcdefghijklmnop", "TOKEN")
        self.check("token ghp_abcdefghijklmnopqrstuvwxyz0123", "ghp_abc", "TOKEN")
        self.check("AKIAABCDEFGHIJKLMNOP", "AKIAABCD", "TOKEN")
        self.check("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456", "abcdefghijklmnop", "AUTH-HEADER")
        self.check("https://user:pa55w0rd@example.com/x", "pa55w0rd", "URL-CREDENTIALS")
        self.check("-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----", "MIIEabc", "KEY")
        self.check("blob " + "A" * 60, "A" * 60, "SECRET")

    def test_key_name_is_kept(self):
        out = egress_redact.redact('api_key="supersecretvalue123" password: hunter2hunter2')
        self.assertIn("api_key=", out)
        self.assertIn("password: ", out)
        self.assertNotIn("supersecretvalue123", out)
        self.assertNotIn("hunter2hunter2", out)

    def test_personal_data(self):
        self.check("mail someone.name@gmail.com now", "someone.name", "EMAIL")
        self.check("call +7 912 345-67-89", "345-67-89", "PHONE")
        self.check("card 4111 1111 1111 1111", "4111 1111", "CARD")
        self.check("ssh 100.104.99.58", "100.104.99.58", "IP")
        self.assertEqual(egress_redact.redact("/Users/alice/Documents/x"), "/Users/user/Documents/x")

    def test_ordinary_text_survives(self):
        text = ("commit 13d1c27, date 2026-10-01T08:27:09Z, count 22348, version 2.1.286, "
                "order 1234567890123 is not a card, /Users/Shared/y")
        self.assertEqual(egress_redact.redact(text), text)

    def test_empty(self):
        self.assertEqual(egress_redact.redact(""), "")
        self.assertEqual(egress_redact.redact(None), "")


if __name__ == "__main__":
    unittest.main()
