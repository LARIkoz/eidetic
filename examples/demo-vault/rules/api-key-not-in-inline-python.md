---
type: rule
title: API key not accessible in inline Python subprocess
aliases: ["api-key-not-in-inline-python"]
tags: ["rule"]
---

# API key not accessible in inline Python subprocess

> Env vars from keys.env not visible inside `python3 -c` when launched from a Bash tool. Use `os.environ` directly or read `keys.env` inside Python.

**Why:** `source` sets vars in bash, but `python3 -c` runs in a subprocess where shell quoting (`\"Bearer {os.environ['MY_API_KEY']}\"`) breaks variable interpolation.

**How to apply:** When calling an API from a Python subprocess — always read `keys.env` inside Python, don't rely on shell env.

## Details

`source keys.env` in bash → `python3 -c "os.environ['MY_API_KEY']"` → KeyError.

**Fix:** Read `keys.env` inside Python:

```python
for line in open(keys_env_path).readlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.strip().split('=', 1)
        os.environ[k] = v.strip('"').strip("'")
```

Or pass as explicit arg: `python3 script.py "$MY_API_KEY"`.

_Confidence: high · Source: my-project_
