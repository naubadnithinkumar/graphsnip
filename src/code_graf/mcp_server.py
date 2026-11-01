"""A small, dependency-free MCP server over stdio (newline-delimited JSON-RPC).

Hand-rolled on purpose: the official SDK's API changed between 1.x and 2.x, and
this server only needs initialize / tools/list / tools/call / ping, which are
stable across protocol versions. It works with Claude Code, GitHub Copilot
(VS Code / CLI) and Codex.
"""
from __future__ import annotations

import json
import sys
import traceback

from code_graf import __version__
from code_graf.engine import Engine

SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = (
    "code-graf answers code questions from a Graphify knowledge graph. Prefer it over grep and whole-file reads: "
    "cg_pack(task) for 'how does X work', cg_find(question) to locate things, cg_symbol(name) for one function/class, "
    "cg_impact(name) for what breaks if it changes, cg_outline(path) before opening a big file."
)

TOOLS = [
    {
        "name": "cg_pack",
        "description": "BEST FIRST CALL for understanding or changing code. Returns a token-budgeted bundle of the exact source "
                       "code most relevant to a task (core symbols in full, supporting code, signatures, related names), "
                       "ranked by the code graph. Use instead of grep + reading several files.",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "What you are trying to understand or change, in plain words."},
            "budget": {"type": "integer", "description": "Max tokens to return (default 4000).", "default": 4000}},
            "required": ["task"]},
    },
    {
        "name": "cg_find",
        "description": "Locate where something lives. Returns a compact, budgeted map of matching symbols, files and how they "
                       "connect (no source code). Follow up with cg_symbol for code.",
        "inputSchema": {"type": "object", "properties": {
            "question": {"type": "string"},
            "budget": {"type": "integer", "default": 1500}}, "required": ["question"]},
    },
    {
        "name": "cg_symbol",
        "description": "Read ONE function, method or class: exact source lines plus what it calls and what calls it. Use instead of "
                       "reading the whole file. Repeat calls for unchanged code return a one-line pointer.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Symbol name, e.g. 'Flask.make_response' or 'path/to/file.py::helper'."},
            "max_lines": {"type": "integer", "default": 120},
            "docs": {"type": "string", "enum": ["short", "full"], "default": "short",
                     "description": "'short' trims long Python docstrings; 'full' keeps them."}},
            "required": ["name"]},
    },
    {
        "name": "cg_impact",
        "description": "Blast radius: what calls, imports, references or inherits from a symbol (reverse traversal). "
                       "Use before changing a function's signature or behaviour.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "depth": {"type": "integer", "default": 2}}, "required": ["name"]},
    },
    {
        "name": "cg_outline",
        "description": "List the symbols in a file with line numbers. Use before reading a large file; then call cg_symbol or "
                       "Read with offset/limit.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "cg_savings",
        "description": "Show tokens saved so far in this project, versus a grep-and-read baseline.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call(engine: Engine, name: str, args: dict) -> tuple[str, bool]:
    if name == "cg_pack":
        r = engine.pack(str(args.get("task", "")), budget=int(args.get("budget", 4000)))
    elif name == "cg_find":
        r = engine.find(str(args.get("question", "")), budget=int(args.get("budget", 1500)))
    elif name == "cg_symbol":
        r = engine.symbol(str(args.get("name", "")), max_lines=int(args.get("max_lines", 120)),
                          docs=str(args.get("docs", "short")))
    elif name == "cg_impact":
        r = engine.impact(str(args.get("name", "")), depth=int(args.get("depth", 2)))
    elif name == "cg_outline":
        r = engine.outline(str(args.get("path", "")))
    elif name == "cg_savings":
        r = engine.savings()
    else:
        return f"Unknown tool: {name}", True
    return r.text, (not r.ok and name not in ("cg_find",))


def handle(engine: Engine, msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if mid is None:  # notification
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        ver = want if want in SUPPORTED else SUPPORTED[0]
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": ver,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "code-graf", "version": __version__},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            text, is_err = _call(engine, str(params.get("name")), params.get("arguments") or {})
        except Exception as e:  # never crash the server on a bad call
            sys.stderr.write(traceback.format_exc())
            text, is_err = f"code-graf error: {type(e).__name__}: {e}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": is_err}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve(root: str = ".") -> None:
    engine = Engine(root, source="agent")
    sys.stderr.write(f"[code-graf] MCP server ready (root={engine.p.root}, session={engine.session})\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if isinstance(msg, list):  # batch
            out = [r for r in (handle(engine, m) for m in msg if isinstance(m, dict)) if r]
            if out:
                sys.stdout.write(json.dumps(out) + "\n")
                sys.stdout.flush()
            continue
        if not isinstance(msg, dict):
            continue
        resp = handle(engine, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
