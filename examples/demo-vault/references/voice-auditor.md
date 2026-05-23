---
type: reference
title: Voice — auditor
aliases: ["voice-auditor"]
tags: ["reference"]
---

# Voice — auditor

_Reference:_ Auditor (Phase 2), Mechanical Audit (Phase 2.5).

## Details

# Auditor (Phase 2)

**Only runs at tier ≥ standard.**

```bash
CODEX_HOME=~/.codex2 codex exec \
  --skip-git-repo-check \
  -c model_reasoning_effort=xhigh \
  -c model_reasoning_summary=detailed \
  -o "$AUDIT_VERDICT.tmp" \
  - < "$AUDIT_BUNDLE"
```

**Models:** Codex 5.4 (hardcoded in script, via acc#2).

**Task:** Verify voice claims against source material, flag:

- Schema enum violations (severity must be: blocker / high / med / low / info)
- Hallucinations / invented findings
- Misattributions (voice claim not grounded in any voice output)
- Contradictions between voices

**Output:** `AUDIT_VERDICT.md` with:

- Per-finding analysis
- `## Overall:` line with `OK / ISSUES / INVALID / RE-SYNTHESIZE`

# Mechanical Audit (Phase 2.5)

**Always runs, all tiers.**

```bash
bash "$SCRIPT_DIR/lib/mechanical_audit.sh" "$DIR" 2>>"$STDERR_LOG"
```

**Deterministic checks:**

1. **URL fetch** — cite each URL, document 200 / 404 / auth / rate responses
2. **Attribution** — match each finding quote to source voice file
3. **Schema normalization** — convert critical → blocker, medium → med, minor → low, severe → high
4. **Severity validation** — ensure all findings use schema enum
5. **Blockquote parsing** — handle `> "quote"\n> — v-<name>.md` format

**Output:** `MECHANICAL_AUDIT.md` with findings table + `## Overall mechanical verdict: PASS|FAIL|CRASH|ISSUES`

**Strict mode:** If `--strict` flag passed AND verdict is FAIL / CRASH / ISSUES, exit code 2.

Related: [[voice-system-architecture]], [[voice-synthesizer]], [[voice-redteam]], [[consilium-synth-hallucinations]].

_Confidence: high · Source: my-project_
