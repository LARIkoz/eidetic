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

OPERATIONAL_PATTERNS = (
    "state.md",
    "SYNTH_FAILURE",
    "tmp_rescue",
    "session_counter",
    "MEMORY.md",
    "AUDIT_STRUCT",
    "HOLES_CHECK",
    "BLIND_SPOTS",
)

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

def is_operational(filename):
    for pat in OPERATIONAL_PATTERNS:
        if pat in filename:
            return True
    return False


def project_slug_from_path(path):
    m = re.search(r"/\.claude/projects/([^/]+)/memory/", path)
    return m.group(1) if m else None


def passes_gate(filepath, meta, force=False):
    """Quality gate. Returns (passed, reason_if_skipped)."""
    if is_operational(os.path.basename(filepath)):
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
    value = str(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def short_project(project_slug):
    """Compress project slug for filename prefix."""
    if not project_slug:
        return "_global"
    # Project slugs look like "-Users-mikhailkozlov-Documents-cursore-foo".
    # Take the last meaningful segment.
    parts = [p for p in project_slug.split("-") if p]
    if not parts:
        return "_global"
    return slugify(parts[-1]) or "_global"


def build_filename(name_slug, project_slug):
    proj = short_project(project_slug)
    base = slugify(name_slug) or "untitled"
    return "{}--{}.md".format(proj, base)


def original_name_slug(meta, filepath):
    raw = get_meta_field(meta, "name") or os.path.basename(filepath).replace(".md", "")
    return slugify(raw)


# ---------- template formatting ----------

WIKILINK_RE = re.compile(r"\[\[([^\[\]\n|#]+)(#[^\[\]\n|]+)?(\|[^\[\]\n]+)?\]\]")


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
    """Pull a 'Label:' value — single line, leave multi-line lists alone."""
    pattern = re.compile(
        r"^[ \t]*(?:\*\*|__)?[ \t]*" + re.escape(label)
        + r"[ \t]*(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*)?[ \t]*(.+?)[ \t]*(?:\*\*)?[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(body)
    if not m:
        return None
    value = m.group(1).strip().rstrip("*").strip()
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


def render_template(meta, body, vault_type, link_map, project_slug, aliases):
    """Returns formatted vault note content."""
    name = get_meta_field(meta, "name") or ""
    description = get_meta_field(meta, "description") or ""
    title = str(name).strip() or str(description).strip() or first_heading(body) or "Untitled"
    confidence = get_meta_field(meta, "confidence", "")
    tags = []
    if vault_type:
        tags.append(vault_type)

    fm_fields = {
        "type": vault_type or "note",
        "title": title,
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
        parts.append("## Details")
        parts.append("")
        parts.append(rest if rest else rewritten_body)
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
    "Hard limit: {max_words} words."
    "\n\n--- ORIGINAL NOTE ---\n{body}\n--- END ORIGINAL ---"
    "\n\nRewritten body:"
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
    # Footer is the last italic _..._ line written by footer()
    if lines and lines[-1].startswith("_") and lines[-1].endswith("_"):
        footer_line = lines[-1]
        body_lines = lines[:-1]
        # Drop trailing blank line before footer
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
    return fm_block, "\n".join(body_lines).strip(), footer_line


def polish_note(content, max_words=400, timeout=60):
    """Rewrite note body via Haiku. Returns rewritten body or None on failure.

    Uses `claude-batch --prompt-file` per claude-cli-runtime contract:
    file-backed transport avoids argv truncation, wrapper telemetry goes to
    stderr, model output is the sole stdout payload.
    """
    _, body, _ = _split_frontmatter_body_footer(content)
    if not body:
        return None
    prompt = POLISH_PROMPT.format(max_words=max_words, body=body)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="eidetic-polish-", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(prompt)
        tmp.close()
        result = subprocess.run(
            ["claude-batch", "--prompt-file", tmp.name,
             "--model", "claude-haiku-4-5-20251001"],
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
    """Rebuild content with polished body + polished:true flag in frontmatter."""
    fm_block, _, footer_line = _split_frontmatter_body_footer(content)
    if not fm_block:
        return content
    fm_inner = fm_block[3:-4].strip("\n")
    fm_lines = [ln for ln in fm_inner.split("\n") if not ln.strip().startswith("polished:")]
    fm_lines.append("polished: true")
    new_fm = "---\n" + "\n".join(fm_lines) + "\n---"
    parts = [new_fm, "", rewritten_body.rstrip()]
    if footer_line:
        parts.extend(["", footer_line])
    return "\n".join(parts).rstrip() + "\n"


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


def write_note(target, folder, filename, content):
    out = Path(target, folder, filename)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(out) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, out)


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


def write_home(target, exported, total, by_type):
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
    # Substring fuzzy match
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


def export(target, project_filter=None, delta=False, force=False,
           polish=False, polish_count=50):
    target = os.path.abspath(os.path.expanduser(target))
    os.makedirs(target, exist_ok=True)

    existing_manifest = load_manifest(target)
    dir_listing = [
        x for x in os.listdir(target)
        if x not in (".manifest.json", ".manifest.json.tmp")
    ]
    if dir_listing and existing_manifest is None and not force:
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
        ok, _reason = passes_gate(filepath, meta, force=force)
        if not ok:
            continue
        parsed.append((filepath, slug, meta, body))

    print("Gate passed: {}.".format(len(parsed)), flush=True)

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
    for filepath, slug, meta, body in parsed:
        vt = get_type(meta)
        if vt:
            folder = TYPE_FOLDER.get(vt, "_unsorted")
            vault_type = VAULT_TYPE.get(vt, "note")
        else:
            folder = "_unsorted"
            vault_type = "note"

        name_slug = original_name_slug(meta, filepath)
        filename = build_filename(name_slug, slug)

        key = (folder, filename)
        if key in used:
            base = filename[:-3]
            i = 2
            while (folder, "{}-{}.md".format(base, i)) in used:
                i += 1
            filename = "{}-{}.md".format(base, i)
            key = (folder, filename)
        used[key] = True

        link_map[name_slug] = filename
        plan.append({
            "filepath": filepath,
            "slug": slug,
            "meta": meta,
            "body": body,
            "folder": folder,
            "vault_type": vault_type,
            "filename": filename,
            "name_slug": name_slug,
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

    for item in plan:
        rel_path = "{}/{}".format(item["folder"], item["filename"])
        sha = sha256_file(item["filepath"])

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
            "source_path": item["filepath"],
        }

        if delta and rel_path in old_files and old_files[rel_path].get("sha256") == sha:
            continue

        aliases = [item["name_slug"]] if item["name_slug"] else []
        content = render_template(
            item["meta"], item["body"], item["vault_type"], link_map, item["slug"], aliases
        )
        write_note(target, item["folder"], item["filename"], content)

    # In delta mode, keep records for files still on disk that we didn't rewrite
    if delta:
        for rel, info in old_files.items():
            if rel not in new_manifest["files"] and os.path.exists(os.path.join(target, rel)):
                new_manifest["files"][rel] = info

    if polish:
        ranked = sorted(
            ((compound_weight(it["meta"], it["filepath"], db_map, it["body"]), it)
             for it in plan),
            key=lambda x: -x[0],
        )
        to_polish = ranked[:polish_count]
        print("Polishing top {} notes via Haiku...".format(len(to_polish)), flush=True)
        polished_count = 0
        for _weight, item in to_polish:
            note_path = os.path.join(target, item["folder"], item["filename"])
            if not os.path.exists(note_path):
                continue
            with open(note_path, "r", encoding="utf-8") as f:
                content = f.read()
            rewritten = polish_note(content)
            if not rewritten:
                continue
            new_content = apply_polished(content, rewritten)
            tmp = note_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp, note_path)
            polished_count += 1
            sys.stdout.write("\r  Polished {}/{}".format(polished_count, len(to_polish)))
            sys.stdout.flush()
        if polished_count:
            print("\n  {} notes polished.".format(polished_count))
        else:
            print("  No notes polished (claude CLI not available or all failed).")

    for folder, notes in folder_notes.items():
        write_moc(target, folder, notes)

    write_home(target, exported_notes, len(exported_notes), by_type_count)
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
    p.add_argument("--polish", action="store_true",
                   help="Rewrite top notes via Haiku for human readability")
    p.add_argument("--polish-count", type=int, default=50,
                   help="Number of notes to polish (default: 50)")
    args = p.parse_args()

    force = args.force and args.all
    try:
        export(args.target_dir, project_filter=args.project, delta=args.delta,
               force=force, polish=args.polish, polish_count=args.polish_count)
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
