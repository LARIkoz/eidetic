#!/usr/bin/env python3
"""Redact secrets and personal data from text before it leaves the machine.

Eidetic sends transcript excerpts to external models (the M3 judge calls a hosted
API). Owner rule 2026-10-01: only redacted text goes out. `redact()` replaces each
match with a typed placeholder, so a reader still sees that something was there.

The judge's verbatim-quote gate compares the model's quote with the spans it was
sent, so callers must redact the spans once and use the redacted spans for both
the prompt and the gate.
"""
from __future__ import annotations

import re

_PLACEHOLDER = "[REDACTED-{}]"

# Order matters: whole blocks and credential-bearing URLs first, so a later, looser
# pattern cannot split them.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("KEY", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)),
    ("URL-CREDENTIALS", re.compile(r"(?<=://)[^\s/:@]+:[^\s/@]+(?=@)")),
    ("AUTH-HEADER", re.compile(r"(?i)(?<=\bauthorization:)\s*(?:bearer|basic|token)\s+[^\s,;'\"]+")),
    ("TOKEN", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("TOKEN", re.compile(
        r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}|gsk_[A-Za-z0-9]{20,}"
        r"|glpat-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|pa-[A-Za-z0-9_-]{30,})")),
    ("SECRET", re.compile(
        r"(?i)(?P<keep>\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|pwd|token)"
        r"[\"']?\s*[:=]\s*)[\"']?[^\s,;'\"]{6,}[\"']?")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("PHONE", re.compile(r"(?<![\w+])\+\d[\d\s().-]{7,}\d(?!\w)")),
    ("IP", re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")),
    ("SECRET", re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{48,}={0,2}(?![A-Za-z0-9+/=_-])")),
)

_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_HOME = re.compile(r"(?<=/)(Users|home)/(?!user\b|Shared\b)[A-Za-z0-9._-]+")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _card_sub(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return _PLACEHOLDER.format("CARD")
    return m.group(0)


def redact(text: str) -> str:
    """Return `text` with secrets and personal data replaced by placeholders."""
    if not text:
        return text or ""
    out = text
    for label, rx in _RULES:
        ph = _PLACEHOLDER.format(label)
        # A rule may keep a leading `keep` group (the key name) and replace only the value.
        out = rx.sub(lambda m, ph=ph: (m.groupdict().get("keep") or "") + ph, out)
    out = _CARD.sub(_card_sub, out)
    out = _HOME.sub(lambda m: f"{m.group(1)}/user", out)
    return out


if __name__ == "__main__":
    import sys
    sys.stdout.write(redact(sys.stdin.read()))
