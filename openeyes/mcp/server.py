"""OpenEyes MCP server — exposes core primitives as MCP tools.

Tools (7):
    list_windows()             -> [{hwnd, title, ...}, ...]
    capture_window(hwnd, out?)  -> {path, width, height}
    detect_elements(hwnd, restore?, depth?, name_contains?, control_type?, regex?)
                                -> [{backend, control_type, name, bbox, center, ...}, ...]
    click(hwnd?, x?, y?, name_contains?, control_type?, regex?,
          button?, double?, dry_run?)
                                -> {clicked: bool, center: {x,y}, target: Element}
    grid(hwnd, row, col, rows?, cols?, dry_run?) -> {center: {x,y}}
    hotkey(combo)               -> {sent: bool, combo: str}
    type_text(text, interval?)  -> {sent_chars: int}

Run:
    eyes-mcp
    # or
    python -m openeyes.mcp.server

Default click is dry_run=true. Set dry_run=false to actually click.
"""
from __future__ import annotations
import asyncio
import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from openeyes import __version__
from openeyes.core.windows import list_windows, find_window
from openeyes.core.capture import capture_window, capture_screen
from openeyes.core.selector import detect_elements, find_elements
from openeyes.core.actuator import click_xy, send_hotkey, type_text

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
                },
                "required": ["text"],
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
            if hwnd:
                img = capture_window(hwnd)
            else:
                img = capture_screen()
            img.save(out, "PNG")
            return [TextContent(type="text", text=_to_json(
                {"path": out, "window": hwnd, "size": list(img.size)}))]

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
            send_hotkey(*keys)
            return [TextContent(type="text", text=_to_json(
                {"sent": True, "combo": combo}))]

        if name == "type_text":
            text = arguments["text"]
            interval = arguments.get("interval", 0.0)
            type_text(text, interval=interval)
            return [TextContent(type="text", text=_to_json(
                {"sent_chars": len(text)}))]

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