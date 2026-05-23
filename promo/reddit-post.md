# Reddit r/ClaudeAI post

**Title:** I built a Second Brain for Claude Code — 124 rules always visible, hybrid search, drift detection, Obsidian vault export, zero core deps

**Body:**

Claude Code forgets everything between sessions. The built-in MEMORY.md has a 200-line platform limit. After 60 sessions I had 500+ memory files and 11 critical rules past line 200 — completely invisible. The agent was violating rules it literally couldn't see.

So I built **Eidetic** — a memory system that compounds knowledge, not just stores it:

**How it works (3 hooks, no config):**

- **Session start (~350ms):** Indexes 500+ files into SQLite FTS5. Assembles 124 behavioral rules + project context into `~/.claude/rules/` (auto-loaded, no size cap). Smart compression: keyword clustering fits 2x more rules in the same token budget.
- **Search:** Hybrid FTS5 + vector (optional fastembed, 33MB). 18/20 recall on our benchmark. Code-aware search via tree-sitter — find functions by meaning.
- **Session end:** Haiku extracts decisions/rules/failures (~$0.002). Before creating new files, searches existing memories — updates instead of duplicating.

**v2.5 — Drift detection (nobody else does this):**

Memories go stale. "Use `validate_key()` in `lib/client.py`" — but someone renamed the file. Eidetic checks wikilinks and age every 24h. Stale memories get demoted in ranking. Feedback rules never expire; project memories decay at 30 days.

**vs competitors:**

|                   | Eidetic   | claude-mem (76K stars) | engram (3.7K) | memsearch (1.8K) |
| ----------------- | --------- | ---------------------- | ------------- | ---------------- |
| Core deps         | **zero**  | ChromaDB               | Node.js       | Milvus           |
| Compounding       | **yes**   | no                     | no            | no               |
| Drift detection   | **yes**   | no                     | no            | no               |
| Token compression | **2.17x** | no                     | no            | no               |
| Auto-updates      | **yes**   | no                     | no            | no               |
| Obsidian export   | **yes**   | no                     | no            | no               |

**New in v4.0 — Obsidian vault export:**

Your AI memory is locked inside `~/.claude/`. You can't browse it, link it, or share it. v4.0 projects it into a real Obsidian vault — the first CLI to do this.

```bash
eidetic export-vault ~/my-vault/
```

- **Quality gate:** 500+ raw files -> ~120 validated knowledge notes (filters out agent junk, half-written drafts, transient state)
- **Template formatting:** Rule Cards, Status Cards, Quick References — human-readable, not an agent dump
- **Auto-MOC** (Map of Content), verified wikilinks, Obsidian graph with color-coded note types
- **Delta mode** for incremental updates — only re-export what changed
- **Demo vault in repo:** `examples/demo-vault/` — 55 notes, 126 wikilinks, ready to open in Obsidian
- Still zero deps. Optional Haiku polish lands in v4.1.

```bash
git clone https://github.com/LARIkoz/eidetic.git && cd eidetic && bash install.sh
```

One command. MIT license. ~2500 lines of Python + bash.

**What I learned building it:**

1. FTS5 alone gets ~60% recall. Vector fallback + tiered query strategy (phrase -> AND -> OR) gets to 90%+
2. Never modify memory files from hooks — race conditions, format corruption, self-reference loops. Keep derived state in SQLite
3. "Zero deps" stops being true with vector search. Be honest: "zero core deps, optional pip for v2 features"

GitHub: https://github.com/LARIkoz/eidetic

Happy to answer architecture questions.
