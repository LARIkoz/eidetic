---
type: rule
title: Silent failures are not OK
aliases: ["silent-failures-are-not-ok"]
tags: ["rule"]
---

# Silent failures are not OK

> If a component "falls off" (returns 0 results, logs error, continues) without surfacing — that's a bug, not degraded service. Build loud-failure guardrails.

**Why:** A session found that `gh search code 'foo language:Python -user:X'` returned HTTP 422 for every language-scoped vector across multiple weekly runs. The runner logged `[gh] error (rc=1)` and moved on. Result: ~2500 queries per bootstrap silently returned zero data. The skill's documented "yield" baseline was calibrated against broken infra — looked normal, was leaking.

## Details

Rule: if a component "falls off" — returns 0 results, logs an error, and the runner continues — that is a BUG in observability, not acceptable degraded service. Silent failures silently eat weeks of work.

**How to apply:**

1. **Any non-transient error is a config bug, not a data point.** HTTP 422 (query parse), HTTP 404 (bad endpoint), HTTP 401 after auth fix — these don't self-heal. Log once, loud, and surface in per-run summary.

2. **Count and ratio, don't just log.** A lone 422 is noise; 50 of them in a 1000-call run is a systemic failure. Track `_errors / _total_calls` ratio and abort (exit-code nonzero) when it crosses threshold.

3. **Classify errors by recoverability:**
   - Transient (rate-limit, timeout, network) → retry with backoff
   - Permanent (422 parse, 404 endpoint, 400 malformed body) → fatal or per-vector quarantine, NEVER silent-continue
   - Unknown (5xx, empty body) → retry once, then treat as permanent

4. **Post-run summary MUST include:**
   - Total calls
   - Successful
   - Errored (broken down by class)
   - List of top-5 error queries for actionable triage

5. **Non-zero exit code on systemic failure.** Cron sees the exit code; silent `exit(0)` while half the vectors failed = pager never fires.

6. **Don't trust absence of errors.** "0 new candidates" looks like "nothing on the network this week" but can hide "every query returned 422." Require the runner to distinguish `query-succeeded-with-0-results` from `query-failed-silently`.

Related: [[deep-research-all-tools-required]] — analogous rule for research pipelines.

_Confidence: high · Source: my-project_
