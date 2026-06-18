---
type: reference
title: JSON Lines Format
aliases: ["json-lines-format"]
tags: ["reference"]
---

# JSON Lines Format

JSON Lines stores one JSON object per line, so a file can be appended to and streamed without parsing the whole thing. It suits logs and append-only event records. Each line is independent, which makes recovery from a partial write trivial.

## Related

- [[markdown-frontmatter]]
- [[one-source-of-truth]]
- [[references-moc]]
