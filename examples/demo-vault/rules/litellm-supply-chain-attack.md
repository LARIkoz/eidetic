---
type: rule
title: LiteLLM supply chain attack 2026-03
aliases: ["litellm-supply-chain-attack"]
tags: ["rule"]
---

# LiteLLM supply chain attack 2026-03

> Supply chain attack via litellm 1.82.7/1.82.8 — lessons, audit results, fixes applied across all projects.

**Why:** Real incident, 1000+ SaaS infected. Discovered accidentally (fork bomb due to attacker's bug).

**How to apply:** On any `pip install` — check what is being installed. Weekly `security-audit.sh`. When updating aider — check litellm version.

## Details

Versions litellm 1.82.7 and 1.82.8 were poisoned via a stolen PyPI token (vector: via Trivy — a security scanner).

### Attack vector

- File `litellm_init.pth` (34 KB) in site-packages
- `.pth` files are automatically executed by Python on ANY interpreter launch
- No `import litellm` needed — just `python`, `pytest`, IDE language server is enough
- Three-layer base64 obfuscation → collects SSH keys, AWS/GCP/Azure creds, K8s Secrets, .env, crypto wallets → AES-256 → POST to spoofed domain
- Persistent backdoor via systemd user-service (survives litellm removal)
- In K8s — privileged Pod on each node

### Response checklist

1. **Audit** — 3 parallel agents: .pth files, keys exposure, pip packages + persistence
2. **Packages updated** — 29 CVE → 3 (aiohttp, cryptography, requests, urllib3, pip, pyasn1, nltk)
3. **keys.env** — chmod 600 (was 644)
4. **`.gitignore`** — added `*.env`, `*.key`, `*.pem` to all repos
5. **`security-audit.sh`** — weekly audit script
6. **`install.sh`** — automatically installs pip-audit + fixes chmod on keys.env
7. **`contract.md`** — Security section deployed to teammates
8. **`CLAUDE.md`** (global + team) — dependency security rules

### Rules derived from the incident

1. **Pin versions** of all Python dependencies. `==`, not `>=`.
2. **Before `pip install --upgrade`** — check changelog and security advisories.
3. **Transitive dependencies are more dangerous than direct** — litellm pulls in via 2000+ packages.
4. **`.pth` files = attack surface** — executed on any Python launch.
5. **Security tools can themselves be the vector** — Trivy was compromised first.
6. **Weekly audit** — `security-audit.sh` (pip-audit + .pth + persistence + key permissions).

_Confidence: high · Source: my-project_
