---
type: project
title: Competitor Teardown Tool
aliases: ["competitor-teardown-tool"]
tags: ["project"]
---

# Competitor Teardown Tool

_Status:_ Competitive teardown framework for mobile apps in a target niche. Pick a category, get a structured breakdown of the top 10 apps. Integrates with the niche-discovery pipeline.

> Tool that reverse-engineers top apps in a niche — UX flow, monetization model, technical stack, design language — and produces a structured breakdown.

## Details

**Status:** SHIPPED v1. Used on 2 niches so far. Quality upgrade in flight.

**Inputs:** category, country, top-N count.
**Outputs:** structured breakdown per app + cross-app comparison + identified gaps.

### Pipeline

1. **Discovery:** App Store scrape top-N by revenue + downloads
2. **Capture:** screenshots of every screen (CDP + simulator)
3. **Metadata:** ASO data (title, subtitle, keywords, description)
4. **UX analysis:** flow extraction from screenshots (vision model)
5. **Monetization:** paywall capture, pricing, free-trial mechanics
6. **Tech stack:** binary inspection — SwiftUI vs UIKit, used SDKs
7. **Synthesis:** per-app breakdown + cross-app comparison + gap list

### Integration with the niche pipeline

- Niche pipeline outputs a category + intent card.
- This tool consumes intent card + scrapes top apps.
- Output gap list feeds back into the niche pipeline as positioning signal.

### Decisions

- **One breakdown per app, not per screen.** Per-screen breakdowns were too granular for product decisions.
- **Cross-app comparison is the main deliverable.** Single-app breakdowns are commodity; the gap synthesis is the value.
- **Use vision model for flow extraction.** Manual extraction takes hours per app. Vision model is 5 min and accurate enough at top-of-funnel.
- **Skip animations.** Static screenshots cover 90% of design language. Animation capture is 10x cost for marginal value.

### Open issues

- Cross-app comparison synthesis quality plateaus at ~6/10. Needs adversarial review step.
- Paywall capture fails on apps with server-driven UI (no static screen to capture).

Related: [[gap-analysis-pipeline]], [[mobile-app-pipeline]].

_Confidence: medium · Source: teardown-tool_
