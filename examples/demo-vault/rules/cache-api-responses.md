---
type: rule
title: Cache API Responses
aliases: ["cache-api-responses"]
tags: ["rule"]
---

# Cache API Responses

Always cache external API responses to a local store before processing them. This protects against rate limits, reduces latency on repeated queries, and preserves raw data for later analysis. A simple approach is writing each response as a timestamped JSON file or a row in a SQLite table keyed by request hash.

## Related

- [[recipe-keeper]]
- [[plant-care-reminder]]
- [[sqlite-wal-mode]]
- [[json-lines-format]]
- [[profile-before-optimising]]
- [[one-source-of-truth]]
