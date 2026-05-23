---
type: rule
title: CDP contention — one scraper per tab
aliases: ["cdp-contention-one-scraper-per-tab"]
tags: ["rule"]
---

# CDP contention — one scraper per tab

> Two parallel CDP Runtime.evaluate scripts on same tab → ~50% error rate + crash risk.

**Why:** A single CDP tab multiplexes `Runtime.evaluate` calls via `id` field. Two clients overlap IDs or fight for response ordering → WS stream garbled. Was masked as "server errors" but server was fine.

## Details

Two parallel Python scripts sending `Runtime.evaluate` + `fetch()` through the same authenticated Chrome tab (port 9222) caused:

- Bulk scraper: 53% error rate (ok=175/err=225 first 400 req)
- Harvester: silently crashed (PID disappeared mid-run)
- All errors cleared as soon as bulk was killed; manual 5/5 probe = 200 OK

**How to apply:**

1. **One CDP scraper per tab at a time.** If need parallelism, open separate tabs/sessions, don't share a tab.
2. When you see >10% error rate on a CDP scraper — first check for another concurrent CDP process, not server rate limit.
3. After a CDP scraper crash with no log output — suspect tab contention, not code bug.
4. If must run two, use separate DevTools targets (different `webSocketDebuggerUrl` per tab).

Applied in a scraping pipeline. Result: kill bulk → harvester restarts cleanly, rate drops from contention-level back to baseline ~15-40 pages/min.

Related: [[silent-failures-are-not-ok]] (the crash had no log output).

_Confidence: high · Source: my-project_
