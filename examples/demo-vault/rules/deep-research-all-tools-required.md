---
type: rule
title: "Deep research — ALL tools required, not partial"
aliases: ["deep-research-all-tools-required"]
tags: ["rule"]
---

# Deep research — ALL tools required, not partial

> Deep research / max-research / "fucking deep" mode — if ANY tool silently fails (gh search, Exa, Perplexity, Gemini, Codex), DO NOT synthesize on remaining data. Stop, diagnose, retry. Partial coverage hidden behind 'synthesis' = confidence artifact, not real signal.

**Why:** Empty `gh search` returning 0 results looks the same as "no data on topic" — silent failure vs. genuine negative. Without `-v` or exit-code inspection, you can't tell. Synthesizing on 4/5 tools creates a **false confidence artifact** — reader sees comprehensive synthesis, doesn't know one source is unverified.

## Details

When user requests deep / max research / "find everything":

**DO NOT proceed with synthesis if any tool silently failed.** Partial coverage hidden behind a synthesis document looks complete but isn't.

**How to apply:**

1. **Detect silent failures upfront** — for each tool:
   - `gh search`: check exit code AND non-empty output AND no "0 results" when topic clearly should match
   - Exa: response JSON has `results[]` non-empty
   - Perplexity: response has `choices[0].message.content` non-empty
   - Gemini CLI: exit 0 AND >100 bytes output AND no "error" / "authentication" strings
   - Codex CLI: exit 0 AND no "401" / "Not logged in" / "quota"

2. **If any tool silently failed → STOP before synthesis:**
   - Announce which tool failed and how
   - Diagnose (auth? quota? DNS? rate limit?)
   - Retry with fix (rotate key, VPN restart, alternative endpoint)
   - Only synthesize when **all 5 returned non-empty, non-error output**

3. **If a tool truly cannot recover in-session:**
   - Explicitly label the synthesis: "⚠️ PARTIAL — GitHub coverage missing, rerun when fixed"
   - List what signals may be absent (e.g., "code patterns from public repos not surveyed")
   - Add TODO to rerun full research once tool restored

4. **Anti-pattern:** "GitHub hunt was empty (`gh search` silent failed) — but Codex + Perplexity + Exa had enough. Writing synthesis:" — this is confidence escalation without data. User reads synthesis as complete; critical gap buried in a throwaway parenthetical.

5. **This mirrors a consilium rule** (flagship + thinking only) — research has the analogous "all 5 tools per spec" rule. Skipping tools ≠ skipping models — both reduce signal. Partial consilium flagged explicitly; partial research must be flagged equally.

**Severity: HIGH** — research outputs drive decisions. Partial input + synthesis wrapper = silent data quality compromise that persists into every downstream action.

Related: [[silent-failures-are-not-ok]], [[research-tool-shape-match]], [[consilium-redteam-mandatory]].

_Confidence: high · Source: my-project_
