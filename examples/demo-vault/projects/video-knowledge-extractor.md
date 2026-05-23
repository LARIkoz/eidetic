---
type: project
title: Video Knowledge Extractor
aliases: ["video-knowledge-extractor"]
tags: ["project"]
---

# Video Knowledge Extractor

_Status:_ Multi-model pipeline that turns long-form video into a structured knowledge base. Groq Whisper + Gemini Vision + Codex methodology extraction. Zero Claude usage in extract path; Claude only at chat layer.

> Take a 1-3 hour interview / talk / podcast → structured methodology + chunked transcript → searchable RAG.

## Details

**Status:** ACTIVE. Major upgrade shipped.
**Public repo:** the project is a public reference implementation.

### Pipeline

| Stage           | Model          | Claude? | Fallback              |
| --------------- | -------------- | ------- | --------------------- |
| Transcription   | Groq Whisper   | no      | Whisper local, Voicee |
| Vision (frames) | Gemini 2.5 Pro | no      | Sonnet (manual)       |
| Methodology     | Codex GPT-5.4  | no      | Opus (manual)         |
| RAG chat        | Claude CLI     | yes     | —                     |

**0 Claude usage** in the extract pipeline by design — Claude reserved for the chat layer where quality matters most and volume is low.

### Strict mode + retry

- Fallback models switch **only manually** via env vars
- Exponential backoff (30s → max 30m)
- `VME_METHODOLOGY_BACKEND=codex|claude`, `VME_VISION_BACKEND=gemini|claude`, `VME_TRANSCRIBER=groq|whisper|voicee`

### Recent upgrade

**Phase 1 DONE:**

- SyntaxError fix in knowledge_base loader
- YAML parser unified (`yaml.safe_load` everywhere)
- Manual tags for first batch of entries
- System prompt clarified: transcript = primary evidence, methodology = AI interpretation

**Phase 2 DONE:**

- Semantic chunking: 128 → 185 transcript chunks, avg 856 chars, no overlap
- Tags in ChromaDB: native list metadata + `$contains` + `--tag` CLI
- A/B ratio test: 5:3 stays (data-driven, 6-4 in favor)
- Query expansion: glossary (19 terms), word-boundary matching
- Methodology prompt: standard abbreviations in English (CPA, LTV, etc.)

**Phase 3 (KB > 10 entries):** auto-tagging via Codex
**Phase 4 (backlog):** Speaker Agent, glossary per-speaker, re-ranking, frame timestamps

### Key decisions

- **Codex > Opus for methodology:** 15 steps / 13 citations vs 9 / 3. Completeness > readability for RAG.
- **Gemini Pro > Haiku for vision:** only one that read all numbers from screen correctly
- **Semantic chunking > sliding window:** no overlap, sentence boundaries, 200-400 tokens
- **Query expansion > threshold relaxation:** CPA 0.71 → 0.52 retrieval gain, no noise
- **Tags: native ChromaDB list** (not delimiter hack). Empty list → None (ChromaDB bug workaround)
- **Ratio 5:3 stays:** A/B test 6-4 in favor, methodology better cosine for structured queries
- **Glossary NOT in ChromaDB:** definitions displace real content

### Gotchas

- **ChromaDB empty list:** `tags: []` → ValueError on `add()`. Filter as None.
- **ChromaDB where:** `$and` + `$eq` required for multiple conditions
- **Gemini CLI vision:** `@/path/to/image.png` (not `--files`)
- **Codex CLI vision:** `--image` flag is unstable
- **Claude CLI subprocess:** does not work from Claude Code bash (not logged in)
- **lru_cache glossary:** does not update without process restart
- **Word boundary:** glossary terms match with `\b` regex, not substring

Related: [[video-kb-portal]], [[parallel-batch-llm]].

_Confidence: high · Source: extractor_
