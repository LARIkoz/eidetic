---
type: project
title: Video KB Portal
aliases: ["video-kb-portal"]
tags: ["project"]
---

# Video KB Portal

_Status:_ RAG portal for a mobile-dev knowledge base — Claude-router + Sonnet synthesis, Tailscale support, security hardened.

> Search + AI-synthesized answers over a transcript-based knowledge base.

## Details

**Why:** A collaborator builds a KB from transcripts and needs convenient search + AI-synthesized answers.

**Architecture:**

- `server.py` (localhost:8788, 0.0.0.0) → Claude-router (Haiku picks topics by meaning) → BM25 within selected → Sonnet synthesis
- Threaded server, security: CORS exact match, Host validation, rate limit 10/min, body 1MB, prompt isolation
- Tailscale: portal accessible on the private network

**Launch:**

- `cd <video-kb-dir> && python3 server.py`
- Desktop: GitHub Pages portal
- Phone: Tailscale IP

**Data:** 10,180 chunks, 117 topics, 18 methodologies, 21 with frames.

**Red team review:** 6 models (Opus, Grok 4, Gemini, Codex, DeepSeek, Qwen), unanimous on 5 critical issues, all fixed.

**How to apply:** When working with the video KB — portal already exists, data updates via `git pull` + restart server.

Related: [[video-knowledge-extractor]], [[consilium-redteam-mandatory]].

_Confidence: high · Source: extractor_
