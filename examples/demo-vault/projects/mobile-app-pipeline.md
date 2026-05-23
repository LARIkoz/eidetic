---
type: project
title: Mobile App Pipeline (concept)
aliases: ["mobile-app-pipeline"]
tags: ["project"]
---

# Mobile App Pipeline (concept)

_Status:_ End-to-end iOS app pipeline — idea to App Store product in one Claude Code chat. Reference-driven SwiftUI, monetization-first, fastlane submit. CONCEPT — implementation details in engineering doc.

> End-to-end pipeline for iOS apps: idea → profitable App Store product (not just code, but a product that makes money).

## Details

End-to-end pipeline for iOS apps in one Claude Code chat.

## Adaptive weight (by FEATURE DEPTH, not screen count)

Complexity = depth of system integrations, not number of screens.

```
SIMPLE (trivial data model, no system APIs):
  Examples: simple timer, unit converter, calculator
  Phase 0: Brief 5 min + competitor screenshots
  Phase 1: Xcode Setup (10 min)
  Phase 2: skip (data model in head)
  Phase 3: 3 visual styles → pick (30 min)
  Phase 4-6: standard
  → 2h PoC → 2 days code → 2 weeks polish + Store

STANDARD (non-trivial model, 1-2 system APIs):
  Examples: Water Tracker, Mood Tracker, Habit Tracker
  Full pipeline (all phases)
  System APIs: HealthKit, Notifications, iCloud, Widgets, StoreKit
  → 1 day plan → 1 week implement → 1-2 weeks polish

COMPLEX (many system APIs, real-time data, CarPlay):
  Examples: OBD Scanner, Coin Analyzer, Symptom Diary + HealthKit
  Full pipeline + HTML wireframe + Codex review
  → 2-3 days plan → 2-3 weeks implement → 1-2 weeks polish
```

## Pipeline

```
Phase 0: Discovery + market research
  ├── Brief (4 pillars: what / for whom / what NOT / first success)
  ├── ASO keyword research (Gemini bg) — affects naming
  ├── Competitor screenshots → vision-based analysis
  ├── Compliance research (by app category)
  ├── Niche Knowledge Base init (gh search → gotchas)
  ├── Monetization model (subscription / one-time / freemium + what's behind paywall)
  └── Output: brief.md, research.md, compliance-checklist.md

Phase 1: Xcode Setup + Foundation
  ├── Project creation + signing
  ├── Privacy Manifests + entitlements
  ├── SPM dependencies + analytics SDK
  └── CLAUDE.md templates (nested)

Phase 2: IA + Data Model (Simple: skip)
  ├── Screen list + navigation flow
  ├── Data model sketch
  └── [IF 10+ screens] HTML wireframe

Phase 3: Design Exploration (FORK)
  ├── Mode A: QUICK (30 min, 7-8/10)
  ├── Mode B: BOOSTED (2-3 hours, 9/10, ~$0-5)
  └── Mode C: AI POLISH LOOP (after A or B)

Phase 4: Implementation
  ├── Core Architecture (Observation + NavigationStack + SwiftData)
  ├── DesignSystem extraction (screen 3 = refactoring)
  ├── Core features + system APIs
  ├── Widgets, Paywall, Analytics, States
  └── Real device testing

Phase 5: Polish (30-50% of total time!)
  ├── Animations, haptics, dark mode
  ├── App Icon, Launch Screen
  └── Accessibility fine-tuning

Phase 6: ASO + Submit
  ├── App Store metadata + screenshots
  └── fastlane deliver → TestFlight → Store

Phase 7: Iteration & Analytics (closing the loop)
  ├── Analytics → backlog
  └── Reviews → v1.1 tasks
```

## Key architectural decisions

### Design System = EXTRACT, not INPUT

**Wrong:** generate DesignSystem.swift from scratch → generic tokens.
**Right:** make 2-3 key screens → approve direction → extract system from real code.

### Reference-driven design

1. Screenshots of top-5 competitors
2. Vision model analyzes: patterns, palettes, navigation
3. Generate SwiftUI inspired by references
4. NOT copying — taking design language

### SwiftUI-first, HTML fallback

- **Main path:** SwiftUI → #Preview → iterate. Design = code.
- **HTML wireframes:** only for complex IA (10+ screens), wireframe level.
- **#Preview:** PRIMARY design tool.

## Cross-cutting systems

| System            | Approach                                                  |
| ----------------- | --------------------------------------------------------- |
| **Monetization**  | Phase 0: model. Phase 4: StoreKit 2. Without = hobby      |
| **Analytics**     | Phase 1: SDK. Phase 4: events. Without = blind            |
| **Widgets**       | For trackers = CORE (not optional). App Groups in Phase 1 |
| **iCloud Sync**   | For sensitive data users expect sync                      |
| **Notifications** | Retention-critical for trackers                           |

## Target apps

| App            | Category       | Weight   | System APIs                       |
| -------------- | -------------- | -------- | --------------------------------- |
| Water Tracker  | Utility        | Standard | HealthKit, Widgets, Notifications |
| Mood Tracker   | Wellness       | Standard | HealthKit, Widgets, iCloud        |
| UV Tracker     | Utility        | Standard | Location, Widgets                 |
| Symptom Diary  | Wellness       | Standard | HealthKit, iCloud                 |
| Travel Journal | Wellness       | Standard | Photos, Location, iCloud          |
| OBD Scanner    | Data/Technical | Complex  | BLE, CarPlay, Widgets             |

## AI role = "best junior"

- AI generates 80% of routine
- Human = architect + polish + "last 10%" magic
- Vibe coding works for v0.1, not for production
- AI useless for: animation curves, haptics, sound design, pixel-perfect

Related: [[gap-analysis-pipeline]], [[competitor-teardown-tool]], [[vibe-coding-research]].

_Confidence: high · Source: my-project_
