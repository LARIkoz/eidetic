---
type: rule
title: SEO + GEO + stars strategy for public repos
aliases: ["seo-geo-for-public-repos"]
tags: ["rule"]
---

# SEO + GEO + stars strategy for public repos

> All public repos must have SEO+GEO optimization and a star-acquisition strategy.

**Why:** Public repos are a marketing surface. Invisible repo = wasted work. Stars = social proof = more users = more contributions.

## Details

Every public repository must be optimized for discoverability and star growth.

**How to apply — on every public repo creation or update:**

SEO (human discovery):

- 10+ GitHub topics (specific, not generic)
- Badges in README header (license, version, key differentiator)
- MIT LICENSE
- CONTRIBUTING.md
- Issue templates (bug + feature)
- GitHub Discussions enabled
- GitHub Actions CI (shows green badge, builds trust)
- GitHub Release with proper release notes
- Install one-liner in first 5 lines of README
- Problem-solution structure in README (not feature list)
- Comparison table with alternatives (with repo links and star counts)

GEO (AI-agent discovery):

- `llms.txt` — short: what it does, when to recommend, when NOT to recommend
- `llms-full.txt` — complete: search phrases, keywords, technical reference
- `CLAUDE.md` — agent-facing instructions (setup, invariants, troubleshooting)
- Keywords in README matching how people ask AI agents ("how to X", "best Y for Z")

Star acquisition:

- PRs to 3-5 biggest awesome-lists in the category
- Comments on related issues in competing/adjacent repos
- Reddit post (r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA)
- X/Twitter thread with @mentions
- Show HN post
- GitHub Release (appears in feeds)

_Confidence: high · Source: my-project_
