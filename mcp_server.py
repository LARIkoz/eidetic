#!/usr/bin/env python3
"""Eidetic MCP Server — Memory tools for any MCP-compatible agent.

Exposes Eidetic memory system as MCP tools over stdio (JSON-RPC).
Works with Claude Code, Cursor, Windsurf, and any MCP client.

Zero external deps — python3 stdlib only.

Usage:
  python3 mcp_server.py
"""

import json
import os
import subprocess
import sys

MEMORY_SYSTEM = os.path.expanduser("~/.claude/memory-system")
BIN = os.path.join(MEMORY_SYSTEM, "bin")

TOOLS = [
    {
        "name": "memory_search",
        "description": "Search long-term memory across all projects. Returns ranked results with compound scoring (evidence × source × freshness). Use when you need past decisions, rules, context, or knowledge.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query, e.g. 'key rotation decision' or 'deployment rules'"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                    "default": 5
                },
                "type_filter": {
                    "type": "string",
                    "description": "Filter by memory type: feedback, project, user, or reference",
                    "enum": ["feedback", "project", "user", "reference", "code"]
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_serendipity",
        "description": "Find unexpected cross-project connections related to a query. Surfaces memories you didn't know were relevant — from other projects, other contexts. Inspired by Zettelkasten: 'The slip-box is designed to surprise you.'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic to find unexpected connections for"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_health",
        "description": "Check Eidetic memory system health: index status, search functionality, hooks, backups.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "memory_reindex",
        "description": "Trigger incremental reindex of memory files. Run after adding or modifying memory files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "full": {
                    "type": "boolean",
                    "description": "Full rebuild instead of incremental (slower but fixes corrupted index)",
                    "default": False
                }
            }
        }
    },
    {
        "name": "memory_lint",
        "description": "Run memory health lint: find orphan files, broken wikilinks, contradiction pairs, and large files that should be split.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "export_vault",
        "description": "Export Eidetic memory to an Obsidian-compatible vault directory. Filters by quality gate, applies templates, writes MOCs and wikilinks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target vault directory (absolute path)"
                },
                "project": {
                    "type": "string",
                    "description": "Optional project slug filter"
                },
                "delta": {
                    "type": "boolean",
                    "description": "Incremental export — only rewrite changed notes",
                    "default": False
                },
                "polish": {
                    "type": "boolean",
                    "description": "Run LLM note polish. Defaults to false for MCP to avoid surprise API calls/timeouts.",
                    "default": False
                },
                "synthesize": {
                    "type": "boolean",
                    "description": "Experimental: run LLM topic synthesis. Defaults to false because v4.3 IA will replace the current topic model.",
                    "default": False
                },
                "polish_count": {
                    "type": "integer",
                    "description": "Number of notes to polish when polish=true (0=all, max 500)",
                    "default": 0
                },
                "polish_model": {
                    "type": "string",
                    "description": "Polish model routing",
                    "enum": ["auto", "sonnet", "haiku"],
                    "default": "auto"
                },
                "all": {
                    "type": "boolean",
                    "description": "Skip quality gate. Implies force=true.",
                    "default": False
                },
                "force": {
                    "type": "boolean",
                    "description": "Allow writing into an existing non-Eidetic directory.",
                    "default": False
                },
                "timeout": {
                    "type": "integer",
                    "description": "Export timeout in seconds (30-1800). Defaults to 60 without LLM and 600 with LLM.",
                    "default": 60
                }
            },
            "required": ["target"]
        }
    }
]


def run_script(script, args=None, timeout=10):
    cmd = [sys.executable, os.path.join(BIN, script)]
    if args:
        cmd.extend(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "ERROR: Script timed out"
    except FileNotFoundError:
        return f"ERROR: Script not found: {script}"


def clamp_limit(value, default=5, max_limit=50):
    try:
        return max(1, min(int(value), max_limit))
    except (TypeError, ValueError):
        return default


def clamp_int(value, default, min_value, max_value):
    try:
        return max(min_value, min(int(value), max_value))
    except (TypeError, ValueError):
        return default


def handle_search(params):
    if not isinstance(params, dict):
        params = {}

    query = str(params.get("query", "")).strip()
    if not query:
        return "ERROR: query is required"

    limit = str(clamp_limit(params.get("limit", 5)))
    args = [os.path.expanduser("~/.claude/memory-system/db/index.db"), query, "--limit", limit, "--json"]
    type_filter = params.get("type_filter")
    if type_filter:
        if type_filter not in {"feedback", "project", "user", "reference", "code"}:
            return f"ERROR: unsupported type_filter: {type_filter}"
        args.extend(["--type", type_filter])
    return run_script("search_impl.py", args, timeout=30)


def handle_serendipity(params):
    query = params.get("query", "")
    return run_script("serendipity.py", [query])


def handle_health(params):
    cmd = [os.path.join(BIN, "health.sh")]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout
    except Exception as e:
        return f"ERROR: {e}"


def handle_reindex(params):
    mode = "--full" if params.get("full") else "--incremental"
    cmd = [os.path.join(BIN, "index.sh"), mode]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout + result.stderr
    except Exception as e:
        return f"ERROR: {e}"


def handle_lint(params):
    return run_script("lint_impl.py")


def handle_export_vault(params):
    target = str(params.get("target", "")).strip()
    if not target:
        return "ERROR: target directory required"
    args = [target]
    project = params.get("project")
    if project:
        args.extend(["--project", str(project)])
    if params.get("delta"):
        args.append("--delta")
    if params.get("all"):
        args.append("--all")
        args.append("--force")
    elif params.get("force"):
        args.append("--force")

    polish = bool(params.get("polish", False))
    synthesize = bool(params.get("synthesize", False))
    if polish:
        polish_model = params.get("polish_model", "auto")
        if polish_model not in {"auto", "sonnet", "haiku"}:
            return f"ERROR: unsupported polish_model: {polish_model}"
        args.extend(["--polish-model", str(polish_model)])
        polish_count = clamp_int(params.get("polish_count", 0), 0, 0, 500)
        if polish_count:
            args.extend(["--polish-count", str(polish_count)])
    else:
        args.append("--no-polish")
    if not synthesize:
        args.append("--no-synthesize")

    timeout_default = 600 if (polish or synthesize) else 60
    timeout = clamp_int(params.get("timeout", timeout_default), timeout_default, 30, 1800)
    cmd = [sys.executable, os.path.join(BIN, "export_vault.py")] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "ERROR: export_vault timed out"
    except FileNotFoundError:
        return "ERROR: Script not found: export_vault.py"
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "non-zero exit"
        return "ERROR (exit {}): {}".format(result.returncode, err)
    return result.stdout


HANDLERS = {
    "memory_search": handle_search,
    "memory_serendipity": handle_serendipity,
    "memory_health": handle_health,
    "memory_reindex": handle_reindex,
    "memory_lint": handle_lint,
    "export_vault": handle_export_vault,
}


def send_response(id, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def handle_request(request):
    method = request.get("method", "")
    id = request.get("id")
    params = request.get("params", {})
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        send_response(id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "eidetic",
                "version": "4.2.2"
            }
        })

    elif method == "notifications/initialized":
        pass

    elif method == "tools/list":
        send_response(id, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = HANDLERS.get(tool_name)

        if not handler:
            send_response(id, error={
                "code": -32601,
                "message": f"Unknown tool: {tool_name}"
            })
            return

        try:
            output = handler(tool_args)
            send_response(id, {
                "content": [{"type": "text", "text": output}]
            })
        except Exception as e:
            send_response(id, {
                "content": [{"type": "text", "text": f"ERROR: {e}"}],
                "isError": True
            })

    elif method == "ping":
        send_response(id, {})

    else:
        if id is not None:
            send_response(id, error={
                "code": -32601,
                "message": f"Method not found: {method}"
            })


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            handle_request(request)
        except json.JSONDecodeError:
            send_response(None, error={
                "code": -32700,
                "message": "Parse error"
            })


if __name__ == "__main__":
    main()
