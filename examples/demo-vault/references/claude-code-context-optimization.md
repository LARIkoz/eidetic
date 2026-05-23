---
type: reference
title: Claude Code context optimization — complete research
aliases: ["claude-code-context-optimization"]
tags: ["reference"]
---

# Claude Code context optimization — complete research

_Reference:_ 3-round deep research (~$9, ~400 citations, 8 sources) on context-window issues — every topic researched to maximum depth.

## Details

## Research stats

- **Exa research-pro:** 6 runs ($6.54, 300 citations)
- **Perplexity deep-research:** 3 runs ($2.56, 130 citations)
- **WebSearch:** 12 queries | **WebFetch:** 5 pages | **GitHub:** 30+ issues full read | **Binary analysis**
- **Total cost:** ~$9.10 | **Total citations:** ~400 unique

---

## 1. Quota formula (CONFIRMED)

```
Quota_per_request = input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens (incl. thinking)

Quota_5h = sum of all requests in rolling 5-hour window
```

| Component                    | Counts toward quota?    | Rate                                 |
| ---------------------------- | ----------------------- | ------------------------------------ |
| input_tokens (post-cache)    | YES                     | Full                                 |
| cache_creation_input_tokens  | YES                     | Full                                 |
| **cache_read_input_tokens**  | **YES (bug/design)**    | **Full rate** (despite 0.1x billing) |
| output_tokens                | YES                     | Full                                 |
| thinking / extended-thinking | YES (counted as output) | Full (NO 3x multiplier)              |

- **cache_read at full rate = THE ROOT CAUSE** — 15K CLAUDE.md × 100 turns = 1.5M quota burned just from re-reads
- **No 3x thinking multiplier** — community speculation, not confirmed by any official source
- **All surfaces share one pool** — claude.ai + Claude Code + Desktop = same 5h window
- **Sub-agents share parent quota** — all counted together

Sources: #24147, #45756, platform docs (extended-thinking, token-counting, prompt-caching, rate-limits).

---

## 2. Cache TTL — triple trigger for downgrade

The 1h → 5m cache TTL downgrade has THREE confirmed triggers:

| Trigger                            | Issue  | Mechanism                                                          |
| ---------------------------------- | ------ | ------------------------------------------------------------------ |
| **DISABLE_TELEMETRY=1**            | #45381 | Client can't report telemetry → server doesn't grant 1h TTL        |
| **DISABLE_NONESSENTIAL_TRAFFIC=1** | #45918 | Blocks GrowthBook feature flags → 1h TTL feature flag not received |
| **Extra Usage (isUsingOverage)**   | #43566 | Client function `IuY` suppresses 1h request when overage active    |

- Server-side regression also occurred ~March 6-8 (#46829)
- Hit rate drops: ~90% (1h) → ~50% (5m) → cost × 2.8
- **Fix:** `CLAUDE_CODE_CACHE_TTL=1h` env var (#16442) — explicitly requests 1h regardless
- **Our fix applied:** removed `DISABLE_TELEMETRY` and `DISABLE_NONESSENTIAL_TRAFFIC`, added `CLAUDE_CODE_CACHE_TTL=1h`

---

## 3. Skills — progressive disclosure is broken

| What docs say                                      | What actually happens                     |
| -------------------------------------------------- | ----------------------------------------- |
| Only metadata (name+description) loaded at startup | **Full SKILL.md loaded** into context     |
| ~50-100 tokens per skill                           | **3,000-5,500 tokens per skill** measured |
| Lazy loading on match                              | Everything in system prompt from start    |

- GitHub #14882, #16616, #23522, #15662 — confirmed by multiple users with `/context` measurements
- **NEW BUG #47098:** Skills placed in `messages[0]` user-content, NOT `system[]` prefix → new sessions NEVER hit full cache
- **Our setup: 37 skills × ~4K avg = ~150K tokens** — this alone exceeds the 200K auto-compact default
- **#31935:** `disable-model-invocation: true` does NOT suppress skills from context
- **NOT FIXED** as of v2.1.104

**Immediate action needed:** reduce from 37 to 10-15 global skills.

Related: [[model-benchmarks-reference]], [[research-handoff-ecosystem]].

_Confidence: high · Source: my-project_
