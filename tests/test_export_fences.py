"""Golden test: vault export must NOT corrupt fenced code blocks.

Pins the DESIRED behaviour for W2 (the deterministic markdown passes must be
fenced-code aware). These are RED on current code — rewrite_wikilinks() and
strip_field() run blanket regexes over the whole body with no fence awareness,
so a [[link]] or a "Field:" line inside a ```fence``` is rewritten/stripped.
They go GREEN once the shared fenced-code mask lands.

Verified live 2026-06-17: the corruption is real (export_vault.py:342-407).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import export_vault as ev  # noqa: E402


def test_wikilink_inside_fence_is_preserved():
    # An unresolved [[not-a-link]] in prose would be stripped to plain text;
    # inside a code fence it must survive verbatim (it is example code, not a link).
    body = '```python\nx = "[[not-a-link]]"  # example, not a wikilink\n```\n'
    out = ev.rewrite_wikilinks(body, {})
    assert '"[[not-a-link]]"' in out, f"fenced wikilink corrupted: {out!r}"


def test_field_line_inside_fence_is_preserved():
    # A bare "Why:" line in a fenced template example must NOT be stripped as a field.
    body = "```\nWhy: this line is example content inside a code block\n```\n"
    out = ev.strip_field(body, "Why")
    assert "Why: this line is example content inside a code block" in out, (
        f"fenced field stripped: {out!r}"
    )


def test_wikilink_outside_fence_still_rewritten():
    # Guard against an over-broad fix: real prose links must still resolve.
    body = "See [[real-target]] for details.\n"
    out = ev.rewrite_wikilinks(body, {"real-target": "Real Target.md"})
    assert "[[" in out and "Real Target" in out, f"prose link not rewritten: {out!r}"
