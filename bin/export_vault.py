#!/usr/bin/env python3
"""Eidetic v4.0 — Obsidian Vault Exporter.

Reads memory files from ~/.claude/projects/*/memory/, filters by quality gate,
applies template formatting, writes an Obsidian-compatible vault with MOC and
wikilinks.

Zero external deps in the hot path (PyYAML used opportunistically with regex
fallback). Python 3.8+ compatible.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_GLOB = os.path.expanduser("~/.claude/projects/*/memory/*.md")
DB_PATH = os.path.expanduser("~/.claude/memory-system/db/index.db")

MAX_FILE_SIZE = 50 * 1024
MIN_NOTES_WARNING = 30

EVIDENCE_WEIGHTS = {
    "foundational": 1.0,
    "validated": 0.9,
    "observed": 0.7,
    "hypothesis": 0.4,
    "system": 0.3,
}
SOURCE_WEIGHTS = {
    "user-explicit": 1.0,
    "user-implicit": 0.8,
    "agent-extracted": 0.5,
    "system": 0.3,
}

OPERATIONAL_EXACT = {
    "state.md",
    "SYNTH_FAILURE.md",
    "MEMORY.md",
    "AUDIT_STRUCT.md",
    "HOLES_CHECK.md",
    "BLIND_SPOTS.md",
}
OPERATIONAL_PREFIXES = ("tmp_rescue", "session_counter")

TYPE_FOLDER = {
    "feedback": "rules",
    "project": "projects",
    "reference": "references",
    "user": "profile",
}

VAULT_TYPE = {
    "feedback": "rule",
    "project": "project",
    "reference": "reference",
    "user": "profile",
}

FOLDER_TITLE = {
    "rules": "Rules",
    "projects": "Projects",
    "references": "References",
    "profile": "Profile",
    "_unsorted": "Unsorted",
}

GRAPH_COLORS = {
    "rules": "#e67e22",
    "projects": "#3498db",
    "references": "#95a5a6",
    "profile": "#f1c40f",
    "topics": "#ff0000",
}



# ---------- frontmatter parsing ----------

def _regex_parse(block):
    """Minimal YAML parser for frontmatter — used when PyYAML is unavailable."""
    meta = {}
    nested = None
    for raw_line in block.split("\n"):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indented = raw_line.startswith(" ") or raw_line.startswith("\t")
        stripped = raw_line.strip()
        m = re.match(r"^([\w][\w_-]*)\s*:\s*(.*)$", stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # Strip surrounding quotes
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if val == "" and not indented:
            nested = key
            meta[key] = {}
            continue
        if indented and nested and isinstance(meta.get(nested), dict):
            meta[nested][key] = val
        else:
            nested = None
            meta[key] = val
    return meta


def parse_frontmatter(text):
    """Parse YAML frontmatter. Returns (meta_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].lstrip("\n")
    body = text[end + 4:].lstrip("\n")
    meta = None
    try:
        import yaml  # type: ignore
        meta = yaml.safe_load(block)
    except ImportError:
        meta = None
    except Exception:
        meta = None
    if not isinstance(meta, dict):
        meta = _regex_parse(block)
    return meta or {}, body


def get_type(meta):
    """Root type: wins over nested metadata.type:."""
    root = meta.get("type")
    if isinstance(root, str) and root.strip():
        return root.strip()
    nested = meta.get("metadata")
    if isinstance(nested, dict):
        n = nested.get("type")
        if isinstance(n, str) and n.strip():
            return n.strip()
    return None


def get_meta_field(meta, key, default=None):
    val = meta.get(key)
    if val is not None and val != "":
        return val
    nested = meta.get("metadata")
    if isinstance(nested, dict):
        v = nested.get(key)
        if v is not None and v != "":
            return v
    return default


# ---------- discovery + gate ----------

def is_operational(filepath):
    basename = os.path.basename(filepath)
    if basename in OPERATIONAL_EXACT:
        return True
    return any(basename.startswith(p) for p in OPERATIONAL_PREFIXES)


def project_slug_from_path(path):
    m = re.search(r"/\.claude/projects/([^/]+)/memory/", path)
    return m.group(1) if m else None


def passes_gate(filepath, meta, force=False):
    """Quality gate. Returns (passed, reason_if_skipped)."""
    if is_operational(filepath):
        return False, "operational"
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return False, "stat-failed"
    if size > MAX_FILE_SIZE:
        return False, "too-large"
    if force:
        return True, None
    if not get_type(meta):
        return False, "no-type"
    if not get_meta_field(meta, "description"):
        return False, "no-description"
    return True, None


# ---------- compound weight ----------

def load_db_weights():
    """Build {path: (evidence, source)} map from index.db. Empty if missing."""
    if not os.path.exists(DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA busy_timeout=2000")
        rows = conn.execute(
            "SELECT path, evidence, source FROM memory_chunks"
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for path, evidence, source in rows:
        if path not in out:
            out[path] = (evidence, source)
    return out


def wikilink_count(body):
    return len(re.findall(r"\[\[([^\]]+)\]\]", body or ""))


def body_weight_adjustment(body):
    """Combined multiplier from wikilink density + body length."""
    body = body or ""
    link_bonus = 1.0 + min(wikilink_count(body), 5) * 0.05
    length_score = max(0.5, min(1.0, len(body.strip()) / 500.0))
    return link_bonus * length_score


def compound_weight(meta, path, db_map, body=None):
    evidence = None
    source = None
    if path in db_map:
        evidence, source = db_map[path]
    if not evidence:
        evidence = get_meta_field(meta, "evidence", "observed")
    if not source:
        source = get_meta_field(meta, "source", "user-explicit")
    ev_w = EVIDENCE_WEIGHTS.get(evidence, 0.5)
    src_w = SOURCE_WEIGHTS.get(source, 0.5)
    weight = ev_w * src_w
    if body is not None:
        weight *= body_weight_adjustment(body)
    return weight


# ---------- slug + naming ----------

def slugify(value):
    if not value:
        return ""
    value = str(value).lower().strip()
    # Keep ASCII alnum + Cyrillic (U+0400-U+04FF); replace everything else with '-'
    value = re.sub(r"[^a-z0-9Ѐ-ӿ]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def short_project(project_slug):
    """Compress project slug for filename prefix."""
    if not project_slug:
        return "_global"
    # Project slugs look like "-Users-mikhailkozlov-Documents-cursore-foo".
    # Drop boilerplate path segments and take the last 2 meaningful ones to
    # avoid collisions like gap-pipeline/data-pipeline → 'pipeline'.
    boilerplate = {"users", "mikhailkozlov", "documents", "cursore"}
    parts = [p for p in project_slug.split("-") if p and p.lower() not in boilerplate]
    if not parts:
        return "_global"
    tail = parts[-2:] if len(parts) >= 2 else parts
    return slugify("-".join(tail)) or "_global"


def build_filename(title, name_slug, project_slug):
    """Human-readable filename from title. Obsidian supports spaces."""
    raw = (title or "").strip()
    if not raw or raw.lower() == "untitled":
        raw = (name_slug or "").replace("-", " ").strip()

    # Replace filesystem-unsafe chars
    raw = raw.replace("/", "-")
    raw = re.sub(r'[\\:*?"<>|]', ' — ', raw)
    raw = re.sub(r'\s+', ' ', raw).strip(' —')

    if not raw:
        raw = "Untitled"

    name = title_case(raw)

    if len(name) > 80:
        truncated = name[:77]
        sp = truncated.rfind(' ')
        if sp > 40:
            truncated = truncated[:sp]
        name = truncated.rstrip(' .—-') + '…'

    return name + ".md"


def compute_title(meta, body, name_slug):
    """Resolve display title — same logic used by render_template + build_filename."""
    name = get_meta_field(meta, "name") or ""
    description = get_meta_field(meta, "description") or ""
    title = (
        str(name).strip()
        or str(description).strip()[:100]
        or first_heading(body)
        or (name_slug or "")
        or "Untitled"
    )
    return title_case(title)


def original_name_slug(meta, filepath):
    raw = get_meta_field(meta, "name") or os.path.basename(filepath).replace(".md", "")
    return slugify(raw)


# ---------- template formatting ----------

WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|#]+)(#[^\[\]\n|]+)?(\|[^\[\]\n]+)?\]\]")
# Footer line produced by footer() — used to strip it on polish round-trip
FOOTER_RE = re.compile(r'^_Confidence:.*·.*Source:.*_$')


def rewrite_wikilinks(body, link_map):
    """Rewrite [[target]] → vault filename, or strip if unresolved."""
    def repl(m):
        target = m.group(1).strip()
        section = (m.group(2) or "").strip()
        display = (m.group(3) or "").lstrip("|").strip()
        key = slugify(target)
        mapped = link_map.get(key)
        if mapped:
            stem = mapped[:-3] if mapped.endswith(".md") else mapped
            if display:
                return "[[{}{}|{}]]".format(stem, section, display)
            return "[[{}{}]]".format(stem, section)
        # Strip — plain text fallback (display preferred)
        return display or target
    return WIKILINK_RE.sub(repl, body)


def extract_blockquote_intro(body):
    """First paragraph wrapped as blockquote, return (quote, remainder)."""
    body = body.lstrip("\n")
    if not body:
        return "", ""
    paras = re.split(r"\n\s*\n", body, maxsplit=1)
    first = paras[0].rstrip()
    rest = paras[1] if len(paras) > 1 else ""
    quoted = "\n".join("> " + line for line in first.split("\n"))
    return quoted, rest


def extract_field(body, label):
    """Pull a 'Label:' value — continues past wraps until blank line or next field."""
    pattern = re.compile(
        r"^[ \t]*(?:\*\*|__)?[ \t]*" + re.escape(label)
        + r"[ \t]*(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*)?[ \t]*(.+?)[ \t]*(?:\*\*)?[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(body)
    if not m:
        return None
    value = m.group(1).strip().rstrip("*").strip()
    # Capture continuation lines until next field or blank line
    rest_start = m.end()
    continuation = []
    for line in body[rest_start:].split("\n"):
        stripped = line.strip()
        if not stripped:
            break
        if re.match(r"^(?:\*\*|__)?[\w\s]+(?:\*\*|__)?:", stripped):
            break
        continuation.append(stripped)
    if continuation:
        value += " " + " ".join(continuation)
    return value or None


def strip_field(body, label):
    """Remove a 'Label:' line from body to avoid duplication."""
    pattern = re.compile(
        r"^[ \t]*(?:\*\*|__)?[ \t]*" + re.escape(label)
        + r"[ \t]*(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*)?[ \t]*.+?[ \t]*(?:\*\*)?[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    cleaned = pattern.sub("", body)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def yaml_frontmatter(fields):
    """Render a minimal YAML frontmatter block."""
    out = ["---"]
    for k, v in fields.items():
        if v is None or v == "":
            continue
        if isinstance(v, list):
            if not v:
                continue
            items = ", ".join(json.dumps(x, ensure_ascii=False) for x in v)
            out.append("{}: [{}]".format(k, items))
        elif isinstance(v, (int, float)):
            out.append("{}: {}".format(k, v))
        else:
            s = str(v).replace("\n", " ").strip()
            if any(c in s for c in ":#[]{}&*!|>%@`,") or s.startswith(("- ", "? ", ": ")):
                out.append('{}: "{}"'.format(k, s.replace('"', '\\"')))
            else:
                out.append("{}: {}".format(k, s))
    out.append("---")
    return "\n".join(out)


def footer(confidence, project_slug):
    proj = short_project(project_slug) if project_slug else "global"
    conf = confidence if confidence not in (None, "") else "—"
    return "_Confidence: {} · Source: {}_".format(conf, proj)


def first_heading(body):
    for line in body.split("\n")[:20]:
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def title_case(s):
    words = s.split()
    result = []
    for w in words:
        if w.isupper() and len(w) > 1:
            result.append(w)
        elif w.startswith("--") or w.startswith("("):
            result.append(w)
        elif any(c.isupper() for c in w[1:]):
            result.append(w)
        else:
            result.append(w.capitalize())
    return " ".join(result)


def render_template(meta, body, vault_type, link_map, project_slug, aliases, filename=""):
    """Returns formatted vault note content."""
    name = get_meta_field(meta, "name") or ""
    description = get_meta_field(meta, "description") or ""
    stem_fallback = filename[:-3] if filename.endswith(".md") else filename
    title = (
        str(name).strip()
        or str(description).strip()[:100]
        or first_heading(body)
        or stem_fallback
        or "Untitled"
    )
    title = title_case(title)
    confidence = get_meta_field(meta, "confidence", "")
    tags = []
    if vault_type:
        tags.append(vault_type)
    eidetic_project = short_project(project_slug) if project_slug else "global"
    if eidetic_project == "_global":
        eidetic_project = "global"

    fm_fields = {
        "type": vault_type or "note",
        "title": title,
        "eidetic_project": eidetic_project,
        "aliases": [a for a in aliases if a],
        "tags": tags,
    }

    rewritten_body = rewrite_wikilinks(body, link_map).rstrip()

    parts = [yaml_frontmatter(fm_fields), ""]
    parts.append("# {}".format(title))
    parts.append("")

    if vault_type == "rule":
        if description:
            parts.append("> {}".format(description.strip()))
            parts.append("")
        why = extract_field(rewritten_body, "Why")
        how = extract_field(rewritten_body, "How to apply") or extract_field(
            rewritten_body, "When to apply"
        )
        if why:
            parts.append("**Why:** {}".format(why))
            parts.append("")
        if how:
            parts.append("**How to apply:** {}".format(how))
            parts.append("")
        details_body = rewritten_body
        if why:
            details_body = strip_field(details_body, "Why")
        if how:
            details_body = strip_field(details_body, "How to apply")
            details_body = strip_field(details_body, "When to apply")
        details_body = details_body.strip()
        if details_body:
            parts.append("## Details")
            parts.append("")
            parts.append(details_body)
    elif vault_type == "project":
        if description:
            parts.append("_Status:_ {}".format(description.strip()))
            parts.append("")
        quote, rest = extract_blockquote_intro(rewritten_body)
        if quote:
            parts.append(quote)
            parts.append("")
        # Skip ## Details when body has no remainder beyond the blockquote intro
        details = rest if rest else (rewritten_body if not quote else "")
        if details:
            parts.append("## Details")
            parts.append("")
            parts.append(details)
    elif vault_type == "reference":
        if description:
            parts.append("_Reference:_ {}".format(description.strip()))
            parts.append("")
        parts.append("## Details")
        parts.append("")
        parts.append(rewritten_body)
    elif vault_type == "profile":
        if description:
            parts.append("> {}".format(description.strip()))
            parts.append("")
        parts.append("## Details")
        parts.append("")
        parts.append(rewritten_body)
    else:
        if description:
            parts.append("_{}_".format(description.strip()))
            parts.append("")
        parts.append(rewritten_body)

    parts.append("")
    parts.append(footer(confidence, project_slug))
    return "\n".join(parts).rstrip() + "\n"


# ---------- polish (Haiku rewrite) ----------

POLISH_PROMPT = (
    "You are reformatting an internal AI-agent memory note for a human reader. "
    "Output ONLY the rewritten note body in Markdown. Do not add any preamble, "
    "meta-commentary, status report, or sign-off (no 'Done.', no 'Here is...', "
    "no summaries of what you changed). "
    "Keep ALL facts exactly as written: numbers, names, paths, model IDs, error "
    "codes, dates, links. Remove agent jargon like 'compound_weight', "
    "'evidence tier', 'session signals'. Add ## section headings if there are "
    "3+ distinct points. Preserve code blocks and tables verbatim. "
    "Do not emit a top-level '# Title' heading — the caller re-adds it. "
    "Format rules:\n"
    "- Use markdown headers (##) for sections, not bold text\n"
    "- Keep tables in markdown table format\n"
    "- Preserve all URLs, file paths, and command examples exactly\n"
    "- If the note is already well-structured, make minimal changes\n"
    "Hard limit: {max_words} words."
    "\n\n--- ORIGINAL NOTE ---\n{body}\n--- END ORIGINAL ---"
    "\n\nRewritten body:"
)


def _build_polish_prompt(body, max_words):
    # str.replace avoids KeyError when body contains '{...}' / JSON / dict literals
    return (
        POLISH_PROMPT
        .replace("{max_words}", str(max_words))
        .replace("{body}", body)
    )


def _split_frontmatter_body_footer(content):
    """Returns (fm_block, body, footer_line) — fm_block includes leading/trailing '---'."""
    if not content.startswith("---"):
        return "", content, ""
    end = content.find("\n---", 3)
    if end == -1:
        return "", content, ""
    fm_block = content[:end + 4]
    rest = content[end + 4:].lstrip("\n")
    lines = rest.rstrip().split("\n")
    footer_line = ""
    body_lines = lines
    # Footer is the last line matching footer() output exactly
    if lines and FOOTER_RE.match(lines[-1]):
        footer_line = lines[-1]
        body_lines = lines[:-1]
        # Drop trailing blank line before footer
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
    return fm_block, "\n".join(body_lines).strip(), footer_line


def choose_polish_model(body):
    """Route to Sonnet for complex notes, Haiku for simple ones."""
    lines = body.strip().split('\n')
    has_table = any('|' in l and l.count('|') >= 3 for l in lines)
    has_code = '```' in body
    section_count = sum(1 for l in lines if l.startswith('## ') or l.startswith('### '))
    body_len = len(body.strip())

    # Sonnet for: tables, code blocks, 3+ sections, long notes
    if has_table or has_code or section_count >= 3 or body_len > 2000:
        return "claude-sonnet-4-6"
    # Haiku for: simple short notes
    return "claude-haiku-4-5-20251001"


def polish_note(content, max_words=400, timeout=60, model="claude-sonnet-4-6"):
    """Rewrite note body via Haiku. Returns rewritten body or None on failure.

    Uses `claude-batch --prompt-file` per claude-cli-runtime contract:
    file-backed transport avoids argv truncation, wrapper telemetry goes to
    stderr, model output is the sole stdout payload.
    """
    _, body, _ = _split_frontmatter_body_footer(content)
    if not body:
        return None
    prompt = _build_polish_prompt(body, max_words)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="eidetic-polish-", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(prompt)
        tmp.close()
        result = subprocess.run(
            ["claude-batch", "--prompt-file", tmp.name,
             "--model", model],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if len(out) <= 50:
        return None
    return out


def apply_polished(content, rewritten_body):
    """Rebuild content with polished body + polished:true flag in frontmatter.

    Preserves original frontmatter and footer; re-applies the original '# Title'
    heading (from frontmatter title or pre-polish heading) so Haiku/Sonnet
    rewrites of the body don't drop or corrupt it.
    """
    fm_block, original_body, footer_line = _split_frontmatter_body_footer(content)
    if not fm_block:
        return content
    fm_inner = fm_block[3:-4].strip("\n")
    # Resolve title: frontmatter `title:` field wins, else first '# ...' heading
    meta, _ = parse_frontmatter(content)
    title = (get_meta_field(meta, "title") or first_heading(original_body) or "").strip()
    fm_lines = [ln for ln in fm_inner.split("\n") if not ln.strip().startswith("polished:")]
    fm_lines.append("polished: true")
    new_fm = "---\n" + "\n".join(fm_lines) + "\n---"

    # Strip any leading '# ...' heading from rewritten body — we re-add the canonical one
    body = rewritten_body.lstrip()
    body_lines = body.split("\n")
    if body_lines and body_lines[0].startswith("# "):
        body_lines = body_lines[1:]
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
    body = "\n".join(body_lines).rstrip()

    parts = [new_fm, ""]
    if title:
        parts.extend(["# {}".format(title), ""])
    parts.append(body)
    if footer_line:
        parts.extend(["", footer_line])
    return "\n".join(parts).rstrip() + "\n"


# ---------- topic synthesis ----------

def load_or_generate_clusters(plan, target):
    """Load cached clusters from .clusters.json or generate via LLM."""
    cache_path = os.path.join(target, ".clusters.json")

    # Cache check: existence is authoritative (seed files use this path).
    # Only fall through to LLM if cache is invalid JSON or empty.
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, list) and cached:
                print("  Using cached clusters ({} topics)".format(len(cached)))
                return cached
        except Exception:
            pass

    print("  Generating topic clusters via LLM...", flush=True)
    titles = []
    for item in plan:
        title = item.get("title", "")
        desc = str(item.get("meta", {}).get("description", ""))[:80]
        typ = item.get("vault_type", "")
        proj = short_project(item.get("slug", ""))
        titles.append("{} | {} | {} | {}".format(typ, proj, title, desc))

    prompt = """You are organizing a knowledge vault. Below are {} memory notes (type | project | name | description).

Group them into 15-25 coherent topics. Rules:
1. Each topic = ONE specific thing (not broad like "Database" or "Tools")
2. DO NOT mix unrelated projects unless they share a concrete technique
3. Minimum 3 notes per topic. Notes not fitting any topic = skip
4. Topic names should be specific (e.g. "Gap Pipeline Niche Discovery" not "Pipeline")
5. Output ONLY a JSON array: [{{"topic": "Name", "notes": ["note name 1", ...]}}]
6. No commentary, just JSON.

Notes:
{}""".format(len(titles), "\n".join(titles))

    if not shutil.which("claude-batch"):
        print("  claude-batch not available, skipping clustering")
        return []

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    try:
        tmp.write(prompt)
        tmp.close()
        result = subprocess.run(
            ["claude-batch", "--prompt-file", tmp.name, "--model", "claude-sonnet-4-6"],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            # Strip markdown code fence if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
            clusters = json.loads(text)
            with open(cache_path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(clusters, f, indent=2, ensure_ascii=False)
            os.replace(cache_path + ".tmp", cache_path)
            print("  Generated {} topics, cached to .clusters.json".format(len(clusters)))
            return clusters
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print("  Clustering failed: {}".format(e))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    return []


def cluster_notes(plan, clusters_data):
    """Match plan items to LLM-generated clusters by title."""
    if not clusters_data:
        return {}, plan

    title_map = {}
    for item in plan:
        title_map[item.get("title", "").lower().strip()] = item
        name = item.get("name_slug", "")
        if name:
            title_map[name.lower()] = item

    clusters = {}
    assigned = set()

    for cluster in clusters_data:
        topic = cluster.get("topic", "")
        note_names = cluster.get("notes", [])
        matched = []
        for name in note_names:
            key = name.lower().strip()
            if key in title_map and id(title_map[key]) not in assigned:
                matched.append(title_map[key])
                assigned.add(id(title_map[key]))
            else:
                # Fuzzy: substring match
                for t_key, t_item in title_map.items():
                    if key in t_key and id(t_item) not in assigned:
                        matched.append(t_item)
                        assigned.add(id(t_item))
                        break
        if len(matched) >= 3:
            clusters[topic] = matched

    unclustered = [item for item in plan if id(item) not in assigned]
    return clusters, unclustered


def synthesize_topic(topic_name, notes, target_dir):
    """Merge N notes into one wiki-style topic page via Sonnet."""
    # Sort by weight descending, take top 20 for context
    sorted_notes = sorted(notes, key=lambda n: n.get("weight", 0), reverse=True)
    top_notes = sorted_notes[:20]
    contents = []
    for note in top_notes:
        path = os.path.join(target_dir, note["folder"], note["filename"])
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                contents.append("### Source: {}\n\n{}\n".format(note["title"], f.read()))

    if not contents:
        return None

    combined = "\n---\n".join(contents)
    if len(combined) > 15000:
        # Keep only top notes by weight to fit context
        contents = contents[:20]
        combined = "\n---\n".join(contents)
        if len(combined) > 12000:
            combined = combined[:12000] + "\n\n[... {} more notes omitted ...]".format(
                len(notes) - 20
            )

    prompt = """Synthesize these {} related notes into ONE coherent wiki article about "{}".

Rules:
- Write a clear introduction (2-3 sentences) explaining what this topic covers
- Use ## sections to organize by subtopic
- Preserve ALL specific facts, numbers, dates, file paths, commands
- Cross-reference individual notes with [[Note Title]] wikilinks
- Add a "## Key Rules" section listing the most important rules/decisions
- Add a "## Known Issues" section if any bugs/problems are mentioned
- Max 1500 words. Be concise, not exhaustive.
- Output ONLY the article body. No preamble.

Notes:
{}""".format(len(contents), topic_name, combined)

    if not shutil.which("claude-batch"):
        return None

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="eidetic-synth-", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(prompt)
        tmp.close()
        result = subprocess.run(
            ["claude-batch", "--prompt-file", tmp.name, "--model", "claude-opus-4-6"],
            capture_output=True, text=True, timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    if len(out) <= 100:
        return None
    return out


def write_topic_pages(target, clusters):
    """Generate topic pages and write to topics/ folder."""
    topics_dir = os.path.join(target, "topics")
    os.makedirs(topics_dir, exist_ok=True)

    topic_notes = []
    for topic_name, notes in clusters.items():
        print("  Synthesizing: {} ({} notes)...".format(topic_name, len(notes)), flush=True)

        body = synthesize_topic(topic_name, notes, target)
        if not body:
            print("    Skipped (synthesis failed)")
            continue

        filename = topic_name.replace(" & ", " and ") + ".md"
        filename = re.sub(r'[/\\:*?"<>|]', '-', filename)

        member_links = "\n".join("- [[{}]]".format(n["filename"][:-3]) for n in notes)

        content = """---
type: topic
title: "{topic}"
tags: ["topic", "synthesis"]
source: eidetic
members: {count}
---

# {topic}

{body}

## Source Notes

{links}

---
_Synthesized by Eidetic from {count} individual notes_
""".format(topic=topic_name, count=len(notes), body=body, links=member_links)

        path = os.path.join(topics_dir, filename)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)

        topic_notes.append({
            "filename": filename,
            "folder": "topics",
            "title": topic_name,
            "description": "Synthesized from {} notes".format(len(notes)),
            "weight": 1.0,
            "mtime": 0,
        })

    if topic_notes:
        moc_lines = ["# Topics", ""]
        for tn in topic_notes:
            moc_lines.append("- [[{}]] — {}".format(tn["filename"][:-3], tn["description"]))
        moc_lines.append("")
        moc_path = os.path.join(topics_dir, "_MOC.md")
        tmp = moc_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(moc_lines))
        os.replace(tmp, moc_path)

    return topic_notes


# ---------- manifest ----------

def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def load_manifest(target):
    mf = os.path.join(target, ".manifest.json")
    if not os.path.exists(mf):
        return None
    try:
        with open(mf, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_manifest(target, manifest):
    mf = os.path.join(target, ".manifest.json")
    tmp = mf + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, mf)


# ---------- vault writers ----------

def ensure_folders(target):
    for sub in ("rules", "projects", "references", "profile", "_unsorted"):
        Path(target, sub).mkdir(parents=True, exist_ok=True)


FOLDER_TITLE["topics"] = "Topics"


def write_note(target, folder, filename, content):
    out = Path(target, folder, filename)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(out) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, out)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_moc(target, folder, notes):
    """notes: list of dicts {filename, title, description, weight}."""
    title = FOLDER_TITLE.get(folder, folder.capitalize())
    notes_sorted = sorted(notes, key=lambda n: n.get("weight", 0), reverse=True)
    lines = ["# {}".format(title), ""]
    if not notes_sorted:
        lines.append("_No notes yet._")
    for n in notes_sorted:
        stem = n["filename"][:-3] if n["filename"].endswith(".md") else n["filename"]
        desc = n.get("description") or ""
        if desc:
            lines.append("- [[{}]] — {}".format(stem, desc))
        else:
            lines.append("- [[{}]]".format(stem))
    lines.append("")
    write_note(target, folder, "_MOC.md", "\n".join(lines))


def write_home(target, exported, total, by_type, topic_notes=None):
    parts = [
        "# Eidetic Knowledge Vault",
        "",
        "## Stats",
        "",
        "- Total notes: {}".format(total),
    ]
    for vault_type in ("rule", "project", "reference", "profile", "note"):
        c = by_type.get(vault_type, 0)
        if c:
            parts.append("- {}: {}".format(vault_type, c))
    parts.append("")
    parts.append("## Sections")
    parts.append("")
    parts.append("- [[rules/_MOC|Rules]]")
    parts.append("- [[projects/_MOC|Projects]]")
    parts.append("- [[references/_MOC|References]]")
    parts.append("- [[profile/_MOC|Profile]]")
    parts.append("- [[_unsorted/_MOC|Unsorted]]")
    if topic_notes:
        parts.append("- [[topics/_MOC|Topics]]")
    parts.append("")
    if topic_notes:
        parts.append("## Topics (synthesized)")
        parts.append("")
        for tn in topic_notes:
            stem = tn["filename"][:-3] if tn["filename"].endswith(".md") else tn["filename"]
            parts.append("- [[topics/{}|{}]] — {}".format(stem, tn["title"], tn["description"]))
        parts.append("")
    parts.append("## Recently updated")
    parts.append("")
    recent = sorted(exported, key=lambda n: n.get("mtime", 0), reverse=True)[:10]
    if not recent:
        parts.append("_No notes yet._")
    for n in recent:
        folder = n["folder"]
        stem = n["filename"][:-3] if n["filename"].endswith(".md") else n["filename"]
        parts.append("- [[{}/{}|{}]]".format(folder, stem, n.get("title", stem)))
    parts.append("")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts.append(
        "_Generated by [Eidetic](https://github.com/LARIkoz/eidetic) · {}_".format(today)
    )
    parts.append("")
    out = Path(target, "HOME.md")
    tmp = str(out) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    os.replace(tmp, out)


def write_obsidian_config(target):
    obs = Path(target, ".obsidian")
    if obs.exists():
        return
    obs.mkdir(parents=True, exist_ok=True)
    graph = {
        "colorGroups": [
            {
                "query": "path:{}/".format(folder),
                "color": {"a": 1, "rgb": int(color.lstrip("#"), 16)},
            }
            for folder, color in GRAPH_COLORS.items()
        ],
        "showTags": True,
        "showAttachments": False,
        "showOrphans": True,
    }
    with open(obs / "graph.json", "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)
    app = {"showFrontmatter": True}
    with open(obs / "app.json", "w", encoding="utf-8") as f:
        json.dump(app, f, indent=2)


# ---------- main pipeline ----------

def discover(project_filter=None):
    """Return list of (filepath, project_slug)."""
    out = []
    for path in glob.glob(PROJECTS_GLOB):
        if not path.endswith(".md"):
            continue
        slug = project_slug_from_path(path)
        if project_filter and slug != project_filter:
            continue
        out.append((path, slug))
    return out


def resolve_project_filter(raw, available):
    if not raw:
        return None
    if raw in available:
        return raw
    # Substring fuzzy match — require 3+ chars to avoid noisy matches
    if len(raw) < 3:
        matches = [p for p in available if p == raw]
    else:
        matches = [p for p in available if raw.lower() in p.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print("ERROR: project '{}' not found. Available:".format(raw), file=sys.stderr)
        for p in sorted(available):
            print("  {}".format(p), file=sys.stderr)
        sys.exit(1)
    print("ERROR: project '{}' is ambiguous. Matches:".format(raw), file=sys.stderr)
    for p in sorted(matches):
        print("  {}".format(p), file=sys.stderr)
    sys.exit(1)


def export(target, project_filter=None, delta=False,
           skip_gate=False, allow_existing=False,
           polish=True, polish_count=0, polish_model="auto",
           synthesize=True):
    target = os.path.abspath(os.path.expanduser(target))
    os.makedirs(target, exist_ok=True)

    existing_manifest = load_manifest(target)
    dir_listing = [
        x for x in os.listdir(target)
        if x not in (".manifest.json", ".manifest.json.tmp")
    ]
    if dir_listing and existing_manifest is None and not allow_existing:
        print(
            "ERROR: Target directory exists but was not created by Eidetic. "
            "Use a new directory or add --force.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Scanning...", flush=True)
    all_projects = sorted({
        project_slug_from_path(p)
        for p in glob.glob(PROJECTS_GLOB)
        if project_slug_from_path(p)
    })
    project = resolve_project_filter(project_filter, all_projects)

    candidates = discover(project)
    print("Found {} files.".format(len(candidates)), flush=True)

    parsed = []
    skip_reasons = {}
    for filepath, slug in candidates:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            print("WARN: cannot read {}: {}".format(filepath, e), file=sys.stderr)
            continue
        try:
            meta, body = parse_frontmatter(text)
        except Exception as e:
            print("WARN: frontmatter parse failed for {}: {}".format(filepath, e),
                  file=sys.stderr)
            continue
        ok, reason = passes_gate(filepath, meta, force=skip_gate)
        if not ok:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue
        parsed.append((filepath, slug, meta, body, text))

    print("Gate passed: {}.".format(len(parsed)), flush=True)
    if skip_reasons:
        breakdown = ", ".join(
            "{}={}".format(r, n) for r, n in sorted(skip_reasons.items())
        )
        print("  Skipped: {}".format(breakdown), flush=True)

    if len(parsed) < MIN_NOTES_WARNING:
        print(
            "Your memory is still growing. export-vault works best after 20+ "
            "sessions. Exporting {} notes.".format(len(parsed)),
            flush=True,
        )

    print("Writing vault...", flush=True)

    db_map = load_db_weights()

    # Allocate filenames + build link map
    plan = []
    link_map = {}
    used = {}
    for filepath, slug, meta, body, raw_text in parsed:
        vt = get_type(meta)
        if vt:
            folder = TYPE_FOLDER.get(vt, "_unsorted")
            vault_type = VAULT_TYPE.get(vt, "note")
        else:
            folder = "_unsorted"
            vault_type = "note"

        name_slug = original_name_slug(meta, filepath)
        title = compute_title(meta, body, name_slug)
        filename = build_filename(title, name_slug, slug)

        key = (folder, filename)
        if key in used:
            base = filename[:-3]
            i = 2
            while (folder, "{} ({}).md".format(base, i)) in used:
                i += 1
            filename = "{} ({}).md".format(base, i)
            key = (folder, filename)
        used[key] = True

        # Collision across projects (same name_slug, different source) ⇒ ambiguous.
        # Mark as None so rewrite_wikilinks falls back to plain text instead of
        # silently pointing to the wrong target.
        if name_slug in link_map:
            link_map[name_slug] = None
        else:
            link_map[name_slug] = filename
        plan.append({
            "filepath": filepath,
            "slug": slug,
            "meta": meta,
            "body": body,
            "raw_text": raw_text,
            "folder": folder,
            "vault_type": vault_type,
            "filename": filename,
            "name_slug": name_slug,
            "title": title,
        })

    ensure_folders(target)

    new_manifest = {
        "_version": 1,
        "_exported_by": "eidetic",
        "_exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": {},
    }
    old_files = (existing_manifest or {}).get("files", {}) if delta else {}

    exported_notes = []
    folder_notes = {f: [] for f in TYPE_FOLDER.values()}
    folder_notes["_unsorted"] = []
    by_type_count = {}

    for i, item in enumerate(plan):
        if (i + 1) % 50 == 0:
            print("  Writing {}/{}...".format(i + 1, len(plan)), flush=True)
        rel_path = "{}/{}".format(item["folder"], item["filename"])
        sha = sha256_text(item["raw_text"])

        weight = compound_weight(item["meta"], item["filepath"], db_map, item["body"])
        desc = get_meta_field(item["meta"], "description", "") or ""
        title = (
            get_meta_field(item["meta"], "name")
            or desc
            or os.path.basename(item["filepath"]).replace(".md", "")
        )

        note_record = {
            "filename": item["filename"],
            "folder": item["folder"],
            "title": str(title),
            "description": str(desc),
            "weight": weight,
            "mtime": os.path.getmtime(item["filepath"]),
        }
        folder_notes.setdefault(item["folder"], []).append(note_record)
        exported_notes.append(note_record)
        by_type_count[item["vault_type"]] = by_type_count.get(item["vault_type"], 0) + 1

        new_manifest["files"][rel_path] = {
            "sha256": sha,
            "source_path": item["filepath"].replace(os.path.expanduser("~"), "~"),
        }

        if delta and rel_path in old_files and old_files[rel_path].get("sha256") == sha:
            continue

        aliases = []
        if item["name_slug"]:
            aliases.append(item["name_slug"])
        stem = item["filename"][:-3] if item["filename"].endswith(".md") else item["filename"]
        if stem and stem != item["name_slug"]:
            aliases.append(stem)
        content = render_template(
            item["meta"], item["body"], item["vault_type"], link_map, item["slug"], aliases,
            filename=item["filename"],
        )
        write_note(target, item["folder"], item["filename"], content)

    # In delta mode, keep records for files still on disk that we didn't rewrite.
    # M4 limitation: when sources are added/removed, previously-unresolved wikilinks
    # in untouched notes won't be re-rendered. Run a full export (no --delta) periodically.
    orphans = 0
    if delta:
        current_sources = {item["filepath"] for item in plan}
        for rel, info in old_files.items():
            if rel not in new_manifest["files"] and os.path.exists(os.path.join(target, rel)):
                new_manifest["files"][rel] = info
                src = info.get("source_path", "")
                src_abs = os.path.expanduser(src) if src.startswith("~") else src
                if src_abs and src_abs not in current_sources:
                    orphans += 1
        if orphans:
            print(
                "  {} orphaned notes in vault (source deleted). Remove manually "
                "or re-export without --delta.".format(orphans),
                flush=True,
            )

    if polish:
        # H3: pre-check CLI availability before iterating
        if shutil.which("claude-batch") is None:
            print("  Skipping --polish: 'claude-batch' not found in PATH.", flush=True)
        else:
            ranked = sorted(
                ((compound_weight(it["meta"], it["filepath"], db_map, it["body"]), it)
                 for it in plan),
                key=lambda x: -x[0],
            )
            if polish_count == 0 or polish_count >= len(ranked):
                to_polish = ranked
            else:
                to_polish = ranked[:polish_count]
            total_polish = len(to_polish)
            print("Polishing top {} notes (model={})...".format(total_polish, polish_model), flush=True)
            polished_count = 0
            skipped_already = 0
            consecutive_failures = 0
            aborted = False
            sonnet_count = 0
            haiku_count = 0
            for idx, (_weight, item) in enumerate(to_polish, 1):
                note_path = os.path.join(target, item["folder"], item["filename"])
                if not os.path.exists(note_path):
                    continue
                with open(note_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # H4: skip if already polished (re-runs become idempotent)
                meta, body_text = parse_frontmatter(content)
                if str(meta.get("polished", "")).lower() == "true":
                    skipped_already += 1
                    continue
                if polish_model == "auto":
                    model = choose_polish_model(body_text)
                elif polish_model == "sonnet":
                    model = "claude-sonnet-4-6"
                else:
                    model = "claude-haiku-4-5-20251001"
                print("  Polishing {}/{} ({})...".format(idx, total_polish, model), flush=True)
                # B1: isolate per-note failures so one crash doesn't kill the loop
                try:
                    rewritten = polish_note(content, model=model)
                except Exception as e:
                    print("    WARN: polish raised {}: {}".format(type(e).__name__, e),
                          file=sys.stderr)
                    rewritten = None
                if not rewritten:
                    consecutive_failures += 1
                    # H3: abort after 3 consecutive failures (likely CLI is down)
                    if consecutive_failures >= 3:
                        print("  ABORT: 3 consecutive polish failures — claude-batch "
                              "likely unavailable.", file=sys.stderr)
                        aborted = True
                        break
                    continue
                consecutive_failures = 0
                new_content = apply_polished(content, rewritten)
                tmp = note_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(new_content)
                os.replace(tmp, note_path)
                polished_count += 1
                if "sonnet" in model:
                    sonnet_count += 1
                else:
                    haiku_count += 1
            summary = "  {} polished, {} already polished (skipped).".format(
                polished_count, skipped_already
            )
            if polished_count:
                summary += "\n  Model split: {} Sonnet, {} Haiku".format(sonnet_count, haiku_count)
            if aborted:
                summary += " ABORTED early."
            elif polished_count == 0 and skipped_already == 0:
                summary = "  No notes polished (claude CLI not available or all failed)."
            print(summary)

    topic_notes = []
    if synthesize and not skip_gate:
        if shutil.which("claude-batch") is None:
            print("  Skipping --synthesize: 'claude-batch' not found in PATH.", flush=True)
        else:
            clusters_data = load_or_generate_clusters(plan, target)
            clusters, _unclustered = cluster_notes(plan, clusters_data)
            if clusters:
                print("Synthesizing {} topic pages...".format(len(clusters)), flush=True)
                topic_notes = write_topic_pages(target, clusters)
                if topic_notes:
                    folder_notes["topics"] = topic_notes

    for folder, notes in folder_notes.items():
        write_moc(target, folder, notes)

    write_home(target, exported_notes, len(exported_notes), by_type_count, topic_notes=topic_notes)
    write_obsidian_config(target)
    write_manifest(target, new_manifest)

    short_target = target.replace(os.path.expanduser("~"), "~")
    rules_n = len([n for n in exported_notes if n["folder"] == "rules"])
    proj_n = len([n for n in exported_notes if n["folder"] == "projects"])
    ref_n = len([n for n in exported_notes if n["folder"] == "references"])
    prof_n = len([n for n in exported_notes if n["folder"] == "profile"])
    uns_n = len([n for n in exported_notes if n["folder"] == "_unsorted"])
    print(
        "Exported {} notes to {} ({} rules, {} projects, {} references, "
        "{} profile, {} unsorted)".format(
            len(exported_notes), short_target, rules_n, proj_n, ref_n, prof_n, uns_n
        )
    )


def main():
    p = argparse.ArgumentParser(description="Export Eidetic memory to Obsidian vault.")
    p.add_argument("target_dir")
    p.add_argument("--project")
    p.add_argument("--delta", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-polish", action="store_true",
                   help="Skip Haiku polish (faster, no API calls)")
    p.add_argument("--polish-count", type=int, default=0,
                   help="Number of notes to polish (0=all, default: all)")
    p.add_argument("--polish-model", choices=["auto", "sonnet", "haiku"], default="auto",
                   help="Model for polish: auto (smart routing), sonnet, or haiku")
    p.add_argument("--no-synthesize", action="store_true",
                   help="Skip topic synthesis (faster, no API calls)")
    args = p.parse_args()

    # --all skips the per-note quality gate (export everything).
    # --force allows writing into a non-Eidetic directory.
    # These are independent; --all requires --force as a safety confirmation.
    if args.all and not args.force:
        print("ERROR: --all requires --force to confirm.", file=sys.stderr)
        sys.exit(1)
    skip_gate = args.all
    allow_existing = args.force
    polish_count = min(max(args.polish_count, 0), 500)
    try:
        export(args.target_dir, project_filter=args.project, delta=args.delta,
               skip_gate=skip_gate, allow_existing=allow_existing,
               polish=not args.no_polish, polish_count=polish_count,
               polish_model=args.polish_model,
               synthesize=not args.no_synthesize)
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
