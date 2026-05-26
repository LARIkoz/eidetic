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

def default_memory_system():
    installed_root = os.path.abspath(os.path.dirname(__file__))
    if os.path.exists(os.path.join(installed_root, ".installed.json")):
        return installed_root
    return os.path.expanduser("~/.claude/memory-system")


MEMORY_SYSTEM = os.path.expanduser(
    os.environ.get("EIDETIC_MEMORY_SYSTEM") or default_memory_system()
)
BIN = os.path.join(MEMORY_SYSTEM, "bin")
INDEX_DB = os.path.join(MEMORY_SYSTEM, "db", "index.db")

TOOLS = [
    {
        "name": "memory_search",
        "description": "Search long-term memory across all projects. Returns a structured payload with ranked results, confidence metadata, no_confident_results, lifecycle status, and drift diagnostics. Do not treat weak candidates as usable memory when no_confident_results=true.",
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
                    "description": "Filter by memory type: feedback, project, user, reference, or code",
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
                    "description": "Experimental: run LLM topic synthesis. Defaults to false because a deferred Vault IA pass will replace the current topic model.",
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


def mcp_text(text, is_error=False, structured=None):
    result = {
        "_mcp_result": {
            "content": [{"type": "text", "text": text}],
        }
    }
    if structured is not None:
        result["_mcp_result"]["structuredContent"] = structured
    if is_error:
        result["_mcp_result"]["isError"] = True
    return result


def mcp_error(message):
    return mcp_text(f"ERROR: {message}", is_error=True)


def run_script(script, args=None, timeout=10):
    cmd = [sys.executable, os.path.join(BIN, script)]
    if args:
        cmd.extend(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            return mcp_error(output.strip() or f"{script} exited {result.returncode}")
        return mcp_text(output)
    except subprocess.TimeoutExpired:
        return mcp_error("Script timed out")
    except FileNotFoundError:
        return mcp_error(f"Script not found: {script}")


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
        return mcp_error("query is required")

    limit = str(clamp_limit(params.get("limit", 5)))
    args = [INDEX_DB, query, "--limit", limit, "--json-object"]
    type_filter = params.get("type_filter")
    if type_filter:
        if type_filter not in {"feedback", "project", "user", "reference", "code"}:
            return mcp_error(f"unsupported type_filter: {type_filter}")
        args.extend(["--type", type_filter])

    cmd = [sys.executable, os.path.join(BIN, "search_impl.py")] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return mcp_error("memory_search timed out")
    except FileNotFoundError:
        return mcp_error("Script not found: search_impl.py")

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "non-zero exit"
        return mcp_error(f"memory_search failed: {err}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = result.stdout.strip()[:500] or result.stderr.strip()[:500]
        return mcp_error(f"memory_search returned invalid JSON: {exc}; output={detail}")

    return mcp_text(json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def handle_serendipity(params):
    query = params.get("query", "")
    return run_script("serendipity.py", [query, INDEX_DB])


def handle_health(params):
    cmd = [os.path.join(BIN, "health.sh")]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            return mcp_error(output.strip() or f"health.sh exited {result.returncode}")
        return mcp_text(output)
    except Exception as e:
        return mcp_error(str(e))


def handle_reindex(params):
    full = bool(params.get("full"))
    mode = "--full" if full else "--incremental"
    timeout = 300 if full else 30
    cmd = [os.path.join(BIN, "index.sh"), mode]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout + result.stderr
        if result.returncode != 0:
            return mcp_error(output.strip() or f"index.sh exited {result.returncode}")
        return mcp_text(output)
    except Exception as e:
        return mcp_error(str(e))


def handle_lint(params):
    return run_script("lint_impl.py", [INDEX_DB])


def handle_export_vault(params):
    target = str(params.get("target", "")).strip()
    if not target:
        return mcp_error("target directory required")
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
            return mcp_error(f"unsupported polish_model: {polish_model}")
        args.extend(["--polish-model", str(polish_model)])
        polish_count = clamp_int(params.get("polish_count", 0), 0, 0, 500)
        if polish_count:
            args.extend(["--polish-count", str(polish_count)])
    else:
        args.append("--no-polish")
    if not synthesize:
        args.append("--no-synthesize")
    else:
        args.append("--synthesize")

    timeout_default = 600 if (polish or synthesize) else 60
    timeout = clamp_int(params.get("timeout", timeout_default), timeout_default, 30, 1800)
    cmd = [sys.executable, os.path.join(BIN, "export_vault.py")] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return mcp_error("export_vault timed out")
    except FileNotFoundError:
        return mcp_error("Script not found: export_vault.py")
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "non-zero exit"
        return mcp_error("export_vault exited {}: {}".format(result.returncode, err))
    return mcp_text(result.stdout)


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
                "version": "4.3.0"
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
            if isinstance(output, dict) and "_mcp_result" in output:
                send_response(id, output["_mcp_result"])
            else:
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
