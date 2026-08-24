"""OpenEyes MCP server — exposes core primitives as MCP tools.

Tools (13 = 7 native + 6 browser):
    list_windows()             -> [{hwnd, title, ...}, ...]
    capture_window(hwnd, out?, dry_run?) -> {path, size, captured}
    detect_elements(hwnd, restore?, depth?, name_contains?, control_type?, regex?)
                                -> [{backend, control_type, name, bbox, center, ...}, ...]
    click(hwnd?, x?, y?, name_contains?, control_type?, regex?,
          button?, double?, dry_run?)
                                -> {clicked: bool, center: {x,y}, target: Element}
    grid(hwnd, row, col, rows?, cols?, dry_run?) -> {center: {x,y}}
    hotkey(combo, dry_run?)     -> {sent: bool, combo: str}
    type_text(text, interval?, dry_run?) -> {sent_chars: int}
    browser_launch(dry_run?)    -> {port, pid, profile_dir, launched}
    browser_tabs()              -> [{id, title, url, type}]
    browser_scan()              -> [{backend, control_type, name, bbox, center, hint, ...}]
    browser_click(--hint|--idx|--name-contains, --go?)
                                -> {clicked: bool, center: {x,y}, target: Element}
    browser_type(--text, --hint|--idx|--name-contains?, dry_run?)
                                -> {sent_chars: int, into: str, press_enter: bool}
    browser_shot(--out, dry_run?) -> {path: str, captured: bool}

Run:
    eyes-mcp
    # or
    python -m openeyes.mcp.server

All side-effecting tools default to dry_run=true. Set dry_run=false to execute.
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from openeyes import __version__
from openeyes.core.windows import list_windows, find_window
from openeyes.core.capture import capture_window, capture_screen
from openeyes.core.selector import detect_elements, find_elements
from openeyes.core.actuator import click_xy, send_hotkey, type_text
from openeyes.core.hints import assign_hints
from openeyes.backends import cdp as browser_backend

server = Server("openeyes")


def _to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_windows",
            description="List all visible top-level windows. "
                        "Optional filters: title_contains / class_name / regex.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title_contains": {"type": "string"},
                    "class_name": {"type": "string"},
                    "regex": {"type": "string"},
                },
            },
        ),
        Tool(
            name="capture_window",
            description="Capture a window (or the full screen if hwnd=0) to PNG.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer", "default": 0},
                    "out": {"type": "string", "description": "output PNG path"},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["out"],
            },
        ),
        Tool(
            name="detect_elements",
            description="Enumerate interactive elements in a window via the platform "
                        "accessibility tree. Returns AI-friendly JSON with bbox/center.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "restore": {"type": "boolean", "default": False,
                                "description": "call ShowWindow(SW_RESTORE) first (UWP)"},
                    "depth": {"type": "integer", "default": 12},
                    "name_contains": {"type": "string"},
                    "control_type": {"type": "string"},
                    "regex": {"type": "string"},
                    "max_results": {"type": "integer", "default": 200},
                },
                "required": ["hwnd"],
            },
        ),
        Tool(
            name="click",
            description="Click by coordinates OR by element selector. "
                        "Default dry_run=true (returns target without clicking).",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "name_contains": {"type": "string"},
                    "control_type": {"type": "string"},
                    "regex": {"type": "string"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"],
                               "default": "left"},
                    "double": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="grid",
            description="Click the center of a Vimium-style grid cell on the window.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "row": {"type": "integer", "minimum": 1},
                    "col": {"type": "integer", "minimum": 1},
                    "rows": {"type": "integer", "default": 3},
                    "cols": {"type": "integer", "default": 3},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["hwnd", "row", "col"],
            },
        ),
        Tool(
            name="hotkey",
            description="Press a hotkey chord, e.g. 'ctrl+a' or 'alt+F4'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "combo": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["combo"],
            },
        ),
        Tool(
            name="type_text",
            description="Type a literal string. ASCII only for now.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "interval": {"type": "number", "default": 0.0},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["text"],
            },
        ),        Tool(
            name="browser_launch",
            description="Launch a dedicated Edge with --remote-debugging-port=PORT. "
                        "Returns {port, pid, profile_dir}. Reuse --profile-dir to "
                        "keep cookies across scripts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": "about:blank"},
                    "port": {"type": "integer", "default": 9222},
                    "profile_dir": {"type": "string",
                        "description": "reuse a temp profile dir to keep cookies"},
                    "seed": {"type": "boolean", "default": True},
                    "headless": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="browser_tabs",
            description="List DevTools page targets on the configured port.",
            inputSchema={
                "type": "object",
                "properties": {"port": {"type": "integer", "default": 9222}},
            },
        ),
        Tool(
            name="browser_scan",
            description="DOM probe the active page; returns interactive elements "
                        "with Vimium-style letter hints. Each entry carries "
                        "backend/control_type/name/bbox/center/hint.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "default": 9222},
                    "url": {"type": "string",
                            "description": "navigate first (default: skip)"},
                    "url_contains": {"type": "string"},
                    "name_contains": {"type": "string"},
                    "control_type": {"type": "string"},
                    "max_results": {"type": "integer", "default": 200},
                },
            },
        ),
        Tool(
            name="browser_click",
            description="Click a single element resolved by --hint, --idx, or "
                        "--name-contains. Default dry_run=true; set go=true to "
                        "actually click.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "default": 9222},
                    "hint": {"type": "string",
                             "description": "Vimium-style letter (a, s, aa, ..)"},
                    "idx": {"type": "integer"},
                    "name_contains": {"type": "string"},
                    "control_type": {"type": "string"},
                    "url_contains": {"type": "string"},
                    "go": {"type": "boolean", "default": False},
                },
            },
        ),
        Tool(
            name="browser_type",
            description="Type text into a focused/input element. Optionally "
                        "pass --hint / --idx / --name-contains to focus a "
                        "specific element first. press_enter=true submits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "default": 9222},
                    "text": {"type": "string"},
                    "hint": {"type": "string"},
                    "idx": {"type": "integer"},
                    "name_contains": {"type": "string"},
                    "control_type": {"type": "string"},
                    "press_enter": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="browser_shot",
            description="Capture the page viewport as PNG.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "default": 9222},
                    "out": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["out"],
            },
        ),

    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "list_windows":
            wins = find_window(
                title_contains=arguments.get("title_contains"),
                class_name=arguments.get("class_name"),
                regex=arguments.get("regex"),
            )
            payload = [w.to_dict() for w in wins]
            return [TextContent(type="text", text=_to_json(payload))]

        if name == "capture_window":
            hwnd = arguments.get("hwnd", 0)
            out = arguments["out"]
            dry_run = arguments.get("dry_run", True)
            if dry_run:
                return [TextContent(type="text", text=_to_json({
                    "captured": False,
                    "dry_run": True,
                    "path": out,
                    "window": hwnd,
                }))]
            if hwnd:
                img = capture_window(hwnd)
            else:
                img = capture_screen()
            img.save(out, "PNG")
            return [TextContent(type="text", text=_to_json(
                {"captured": True, "dry_run": False, "path": out,
                 "window": hwnd, "size": list(img.size)}))]

        if name == "detect_elements":
            hwnd = arguments["hwnd"]
            elems = detect_elements(
                hwnd,
                restore=arguments.get("restore", False),
                max_depth=arguments.get("depth", 12),
            )
            elems = find_elements(
                elems,
                name_contains=arguments.get("name_contains"),
                control_type=arguments.get("control_type"),
                regex=arguments.get("regex"),
            )
            elems = elems[: arguments.get("max_results", 200)]
            return [TextContent(type="text", text=_to_json(
                [e.to_dict() for e in elems]))]

        if name == "click":
            dry_run = arguments.get("dry_run", True)
            target = None
            cx = cy = None
            if arguments.get("x") is not None and arguments.get("y") is not None:
                cx, cy = arguments["x"], arguments["y"]
            else:
                hwnd = arguments.get("hwnd")
                if not hwnd:
                    return [TextContent(type="text", text=_to_json(
                        {"error": "need x/y or hwnd"}))]
                elems = detect_elements(hwnd, restore=True)
                matches = find_elements(
                    elems,
                    name_contains=arguments.get("name_contains"),
                    control_type=arguments.get("control_type"),
                    regex=arguments.get("regex"),
                )
                if not matches:
                    return [TextContent(type="text", text=_to_json(
                        {"error": "no matching element", "hwnd": hwnd}))]
                target = matches[0]
                cx, cy = target.center.x, target.center.y
            if not dry_run:
                click_xy(cx, cy, button=arguments.get("button", "left"),
                         double=arguments.get("double", False))
            return [TextContent(type="text", text=_to_json({
                "clicked": not dry_run,
                "center": {"x": cx, "y": cy},
                "target": target.to_dict() if target else None,
            }))]

        if name == "grid":
            import win32gui
            hwnd = arguments["hwnd"]
            row = arguments["row"]
            col = arguments["col"]
            rows = arguments.get("rows", 3)
            cols = arguments.get("cols", 3)
            dry_run = arguments.get("dry_run", True)
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            cell_w = (r - l) / cols
            cell_h = (b - t) / rows
            cx = int(l + (col - 0.5) * cell_w)
            cy = int(t + (row - 0.5) * cell_h)
            if not dry_run:
                click_xy(cx, cy)
            return [TextContent(type="text", text=_to_json({
                "clicked": not dry_run,
                "center": {"x": cx, "y": cy},
                "row": row, "col": col, "rows": rows, "cols": cols,
            }))]

        if name == "hotkey":
            combo = arguments["combo"]
            keys = [k.strip() for k in combo.split("+") if k.strip()]
            dry_run = arguments.get("dry_run", True)
            if dry_run:
                return [TextContent(type="text", text=_to_json({
                    "sent": False,
                    "dry_run": True,
                    "combo": combo,
                    "keys": keys,
                }))]
            send_hotkey(*keys)
            return [TextContent(type="text", text=_to_json(
                {"sent": True, "dry_run": False, "combo": combo}))]

        if name == "type_text":
            text = arguments["text"]
            interval = arguments.get("interval", 0.0)
            dry_run = arguments.get("dry_run", True)
            if dry_run:
                return [TextContent(type="text", text=_to_json({
                    "sent": False,
                    "sent_chars": 0,
                    "would_send_chars": len(text),
                    "dry_run": True,
                }))]
            type_text(text, interval=interval)
            return [TextContent(type="text", text=_to_json(
                {"sent": True, "sent_chars": len(text), "dry_run": False}))]

        if name == "browser_launch":
            from pathlib import Path
            dry_run = arguments.get("dry_run", True)
            port = arguments.get("port", 9222)
            url = arguments.get("url", "about:blank")
            profile_dir = arguments.get("profile_dir")
            seed = arguments.get("seed", True)
            headless = arguments.get("headless", False)
            if dry_run:
                return [TextContent(type="text", text=_to_json({
                    "launched": False,
                    "dry_run": True,
                    "port": port,
                    "url": url,
                    "profile_dir": profile_dir,
                    "seed": seed,
                    "headless": headless,
                }))]
            info = browser_backend.launch_edge(
                port=port,
                url=url,
                profile_dir=(Path(profile_dir) if profile_dir else None),
                seed=seed,
                headless=headless,
            )
            info = {**info, "launched": True, "dry_run": False}
            return [TextContent(type="text", text=_to_json(info))]

        if name == "browser_tabs":
            try:
                tabs = browser_backend.list_tabs(arguments.get("port", 9222))
            except Exception as e:
                return [TextContent(type="text", text=_to_json({"error": str(e)}))]
            short = [{"id": t.get("id"), "title": t.get("title"),
                      "url": t.get("url"), "type": t.get("type")}
                     for t in tabs]
            return [TextContent(type="text", text=_to_json(short))]

        if name == "browser_scan":
            try:
                conn = browser_backend.connect(
                    port=arguments.get("port", 9222),
                    url_contains=arguments.get("url_contains"),
                )
                if arguments.get("url"):
                    conn.navigate(arguments["url"])
                    time.sleep(0.6)
                elems = browser_backend.scan_dom(conn)
                assign_hints(elems)
                if arguments.get("name_contains"):
                    elems = [e for e in elems
                             if arguments["name_contains"].lower() in (e.name or "").lower()]
                if arguments.get("control_type"):
                    elems = [e for e in elems
                             if e.control_type == arguments["control_type"]]
                elems = elems[:arguments.get("max_results", 200)]
                return [TextContent(type="text", text=_to_json(
                    [e.to_dict() for e in elems]))]
            except Exception as e:
                return [TextContent(type="text", text=_to_json(
                    {"error": str(e), "tool": name}))]

        if name == "browser_click":
            try:
                conn = browser_backend.connect(
                    port=arguments.get("port", 9222),
                    url_contains=arguments.get("url_contains"),
                )
                elems = browser_backend.scan_dom(conn)
                assign_hints(elems)
                chosen = None
                if arguments.get("hint"):
                    for e in elems:
                        if e.hint and e.hint.lower() == arguments["hint"].lower():
                            chosen = e
                            break
                elif arguments.get("idx") is not None:
                    i = arguments["idx"]
                    if 0 <= i < len(elems):
                        chosen = elems[i]
                elif arguments.get("name_contains"):
                    for e in elems:
                        if arguments["name_contains"].lower() in (e.name or "").lower():
                            chosen = e
                            break
                elif arguments.get("control_type"):
                    for e in elems:
                        if e.control_type == arguments["control_type"]:
                            chosen = e
                            break
                if not chosen and elems:
                    chosen = elems[0]
                if not chosen:
                    return [TextContent(type="text", text=_to_json(
                        {"error": "no element matched", "tool": name}))]
                go = arguments.get("go", False)
                if not go:
                    return [TextContent(type="text", text=_to_json({
                        "clicked": False,
                        "would_click": True,
                        "center": {"x": chosen.center.x, "y": chosen.center.y},
                        "target": chosen.to_dict(),
                    }))]
                browser_backend.click_center(conn, chosen)
                return [TextContent(type="text", text=_to_json({
                    "clicked": True,
                    "center": {"x": chosen.center.x, "y": chosen.center.y},
                    "target": chosen.to_dict(),
                }))]
            except Exception as e:
                return [TextContent(type="text", text=_to_json(
                    {"error": str(e), "tool": name}))]

        if name == "browser_type":
            try:
                dry_run = arguments.get("dry_run", True)
                text = arguments["text"]
                press_enter = bool(arguments.get("press_enter", False))
                has_target = any(arguments.get(k) is not None for k in
                                 ("hint", "idx", "name_contains", "control_type"))
                if dry_run and not has_target:
                    return [TextContent(type="text", text=_to_json({
                        "sent": False,
                        "sent_chars": 0,
                        "would_send_chars": len(text),
                        "into": "(focused)",
                        "press_enter": press_enter,
                        "dry_run": True,
                    }))]
                conn = browser_backend.connect(port=arguments.get("port", 9222))
                chosen = None
                if has_target:
                    elems = browser_backend.scan_dom(conn)
                    assign_hints(elems)
                    if arguments.get("hint"):
                        for e in elems:
                            if e.hint and e.hint.lower() == arguments["hint"].lower():
                                chosen = e
                                break
                    elif arguments.get("idx") is not None:
                        i = arguments["idx"]
                        if 0 <= i < len(elems):
                            chosen = elems[i]
                    elif arguments.get("name_contains"):
                        for e in elems:
                            if arguments["name_contains"].lower() in (e.name or "").lower():
                                chosen = e
                                break
                    elif arguments.get("control_type"):
                        for e in elems:
                            if e.control_type == arguments["control_type"]:
                                chosen = e
                                break
                    if not chosen and elems:
                        chosen = elems[0]
                if not dry_run:
                    browser_backend.type_text(
                        conn, text, element=chosen, press_enter=press_enter,
                    )
                return [TextContent(type="text", text=_to_json({
                    "sent": not dry_run,
                    "sent_chars": 0 if dry_run else len(text),
                    "would_send_chars": len(text) if dry_run else None,
                    "into": chosen.name if chosen else "(focused)",
                    "press_enter": press_enter,
                    "dry_run": dry_run,
                    "target": chosen.to_dict() if chosen else None,
                }))]
            except Exception as e:
                return [TextContent(type="text", text=_to_json(
                    {"error": str(e), "tool": name}))]

        if name == "browser_shot":
            try:
                out = arguments["out"]
                if arguments.get("dry_run", True):
                    return [TextContent(type="text", text=_to_json({
                        "captured": False,
                        "dry_run": True,
                        "path": out,
                    }))]
                conn = browser_backend.connect(port=arguments.get("port", 9222))
                path = browser_backend.screenshot(conn, out)
                return [TextContent(type="text", text=_to_json({
                    "captured": True,
                    "dry_run": False,
                    "path": path,
                }))]
            except Exception as e:
                return [TextContent(type="text", text=_to_json(
                    {"error": str(e), "tool": name}))]
        return [TextContent(type="text", text=_to_json(
            {"error": f"unknown tool: {name}"}))]
    except Exception as e:
        return [TextContent(type="text", text=_to_json(
            {"error": str(e), "tool": name, "args": arguments}))]


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


def main() -> int:
    """Console entrypoint: eyes-mcp."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
