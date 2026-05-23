---
type: rule
title: Prefer Exa over WebFetch for JS-rendered pages
aliases: ["exa-over-webfetch"]
tags: ["rule"]
---

# Prefer Exa over WebFetch for JS-rendered pages

> Use Exa API search+contents instead of WebFetch for flight/travel/JS-rendered pages.

**Why:** WebFetch fails on JS-rendered pages (Aviasales, Yandex Travel, Booking.com) — returns only CSS/JS scaffold. Exa neural search + content extraction works reliably.

**How to apply:** For any web research — use Exa `/search` with `contents.text` first. Fall back to WebFetch only for static pages or direct HTML content.

## Details

Prefer Exa API (`/search` with `contents`) over WebFetch for research tasks, especially travel/booking sites.

Related: [[research-tool-shape-match]] (don't fire heavy research tools when a simple fetch will do).

_Confidence: high · Source: my-project_
