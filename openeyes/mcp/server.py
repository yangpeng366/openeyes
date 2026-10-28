"""
OpenEyes MCP server — exposes core primitives as MCP tools.

Tools (15 = 7 native + 6 browser + 2 structured-first):
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
    click_by_id(hwnd, automation_id, method?, dry_run?)
                                -> {ok, method, name, center, landed, error}
    act_and_verify(hwnd, automation_id, expect, method?, retries?, timeout?)
                                -> {ok, attempts, observed, elapsed, error}
    native_surfaces(app, cli_names?, schemes?, exe_names?)
                                -> {found, surfaces: [{kind, target, command}], ...}
    resolve_elements(hwnd?, port?, url_contains?, detections_path?)
                                -> {used_backend, attempts: [...], elements: [...]}
    vision_detect(detections_path?|--detections, origin_x?, origin_y?, score?)
                                -> [{backend, control_type, name, bbox, center, ...}, ...]
    app_ensure(title_contains?, class_name?, process_name?, exe?, aumid?,
              timeout?, dry_run?)
                                -> {ok, state, action, hwnd, pid, ...}

Run:
    eyes-mcp
    # or
    python -m openeyes.mcp.server

All side-effecting tools default to dry_run=true. Set dry_run=false to execute.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from openeyes.backends import cdp as browser_backend
from openeyes.core.actuator import click_xy, send_hotkey, type_text
from openeyes.core.capture import capture_screen, capture_window
from openeyes.core.hints import assign_hints
from openeyes.core.safety import is_foreground_window, validate_text_entry
from openeyes.core.selector import detect_elements, find_elements
from openeyes.core.windows import find_window

server = Server("openeyes")


def _to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)

def _load_detections(arguments: dict) -> tuple[Any, str | None]:
    """Read screen-parsing detections from a file path or inline JSON.

    Returns ``(payload, error)``; ``error`` is None on success.
    """
    path = arguments.get("detections_path")
    if path:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8-sig")), None
        except OSError as exc:
            return None, f"cannot read detections file {path!r}: {exc}"
        except json.JSONDecodeError as exc:
            return None, f"invalid detection JSON in {path!r}: {exc}"
    inline = arguments.get("detections")
    if inline:
        try:
            return json.loads(inline), None
        except json.JSONDecodeError as exc:
            return None, f"invalid inline detection JSON: {exc}"
    return None, "need detections_path or detections"


def _match_elements(hwnd: int, selector: dict) -> list:
    """Elements of a window matching a selector dict.

    A selector may carry ``automation_id`` (the stable anchor), plus
    ``name_contains`` / ``control_type`` / ``regex``.
    """
    if not selector:
        return []
    elems = detect_elements(hwnd, restore=True)
    return find_elements(
        elems,
        automation_id=selector.get("automation_id"),
        name_contains=selector.get("name_contains"),
        control_type=selector.get("control_type"),
        regex=selector.get("regex"),
    )


def _observe(hwnd: int, expect: dict) -> dict:
    """Read the live state an expectation talks about.

    Returns ``{"satisfied": bool, "detail": str, "evidence": {...}}``. The
    evidence names what was actually seen, so a miss can be explained
    instead of merely reported.
    """
    if not isinstance(expect, dict) or not expect:
        return {"satisfied": False,
                "detail": "empty expectation: it needs element_appears, "
                          "element_vanishes or label_matches",
                "evidence": {}}

    appears = expect.get("element_appears")
    vanishes = expect.get("element_vanishes")
    label = expect.get("label_matches")

    if appears:
        hits = _match_elements(hwnd, appears)
        return {
            "satisfied": bool(hits),
            "detail": ("element appeared" if hits else
                       "element did not appear"),
            "evidence": {"selector": appears,
                         "names": [e.name for e in hits[:5]]},
        }

    if vanishes:
        hits = _match_elements(hwnd, vanishes)
        return {
            "satisfied": not hits,
            "detail": ("element vanished" if not hits else
                       "element is still there"),
            "evidence": {"selector": vanishes,
                         "names": [e.name for e in hits[:5]]},
        }

    if label:
        selector = {k: v for k, v in label.items() if k != "regex"}
        pattern = label.get("regex")
        hits = _match_elements(hwnd, selector)
        names = [e.name or "" for e in hits]
        matched = [n for n in names
                   if pattern is None or re.search(pattern, n, re.IGNORECASE)]
        return {
            "satisfied": bool(matched),
            "detail": ("label matched" if matched else
                       "label did not match"),
            "evidence": {"selector": selector, "regex": pattern,
                         "names": names[:5], "matched": matched[:5]},
        }

    return {"satisfied": False,
            "detail": "expectation needs element_appears, element_vanishes "
                      "or label_matches",
            "evidence": {}}


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
                    "depth": {"type": "integer", "default": 30},
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
                    "backend": {"type": "string", "enum": ["edge", "lightpanda"],
                        "default": "edge",
                        "description": "edge = persistent cookies (default); lightpanda = WSL2+Docker, ~123 MB RAM, no persistent profile."},
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
                    "source_text": {
                        "type": "string",
                        "description": "user text that authorizes text",
                    },
                    "url_contains": {"type": "string"},
                    "press_enter": {"type": "boolean", "default": False},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="browser_shot",
            description="Capture the page viewport as PNG. Pass "
                        "--url-contains to scope capture to one page target.",
            inputSchema={
                "type": "object",
                "properties": {
                    "port": {"type": "integer", "default": 9222},
                    "out": {"type": "string"},
                    "url_contains": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["out"],
            },
        ),
        Tool(
            name="resolve_elements",
            description="Walk the structured-first backend chain (uia -> cdp -> vision) "
                        "and return the first non-empty element set, which tier answered, "
                        "and why the earlier tiers fell through. Read-only.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer",
                             "description": "native window handle; enables the uia tier"},
                    "port": {"type": "integer", "default": 9222,
                             "description": "CDP port; 0 skips the cdp tier"},
                    "url_contains": {"type": "string"},
                    "detections_path": {"type": "string",
                                        "description": "screen-parsing detection JSON; enables the vision tier"},
                    "origin_x": {"type": "integer", "default": 0},
                    "origin_y": {"type": "integer", "default": 0},
                    "max_results": {"type": "integer", "default": 200},
                },
            },
        ),
        Tool(
            name="vision_detect",
            description="Parse screen-parsing detections (OmniParser v2 compatible) into "
                        "AI-friendly elements with bbox/center. Read-only; no model runs "
                        "on this host.",
            inputSchema={
                "type": "object",
                "properties": {
                    "detections_path": {"type": "string",
                                        "description": "path to a detection JSON file"},
                    "detections": {"type": "string",
                                   "description": "inline detection JSON (used when detections_path is absent)"},
                    "origin_x": {"type": "integer", "default": 0},
                    "origin_y": {"type": "integer", "default": 0},
                    "score": {"type": "number", "default": 0.9},
                    "max_results": {"type": "integer", "default": 200},
                },
            },
        ),
        Tool(
            name="app_ensure",
            description="Ensure an app is alive and usable, then prove it. "
                        "Reports whether the app is running, window-gone or not "
                        "running; restores a hidden window without stealing focus, "
                        "or launches it (exe / Store AUMID) and waits until it is "
                        "actually usable. ok is true only when usability was "
                        "observed, not merely when a launch was issued.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title_contains": {"type": "string",
                                        "description": "substring of the window title"},
                    "class_name": {"type": "string"},
                    "process_name": {"type": "string",
                                       "description": "process name, with or without .exe"},
                    "exe": {"type": "string",
                            "description": "executable to start when the app is gone"},
                    "aumid": {"type": "string",
                            "description": "Store app id, e.g. Family!App"},
                    "timeout": {"type": "number", "default": 30.0},
                    "dry_run": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="click_by_id",
            description="Click an element addressed by its stable automation_id. "
                        "Use this instead of click-by-name when the visible label "
                        "changes (a live countdown, a spinner): the automation id "
                        "is the anchor that does not move. Prefers the UIA Invoke "
                        "pattern, so it needs no pixel maths, does not move the "
                        "user's cursor and does not steal focus. "
                        "Default dry_run=true (resolves the target, clicks nothing).",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "automation_id": {"type": "string"},
                    "control_type": {"type": "string"},
                    "method": {"type": "string", "enum": ["auto", "invoke", "pixel"],
                               "default": "auto",
                               "description": "auto tries Invoke first and falls "
                                              "back to a pixel click"},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["hwnd", "automation_id"],
            },
        ),
        Tool(
            name="act_and_verify",
            description="Act, then prove the act worked. Runs an action and "
                        "reports ok=true ONLY when the expected state was "
                        "observed - never merely because the action was "
                        "delivered. The expectation is read from the live "
                        "accessibility tree (an element that must appear or "
                        "vanish, or a control whose label must match), so a "
                        "click that changed nothing is a miss, not a success.",
            inputSchema={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer"},
                    "automation_id": {"type": "string",
                                      "description": "the element to act on"},
                    "control_type": {"type": "string"},
                    "name_regex": {"type": "string",
                                   "description": "fallback selector when the "
                                                  "automation_id is absent"},
                    "method": {"type": "string", "enum": ["auto", "invoke", "pixel"],
                               "default": "auto"},
                    "expect": {
                        "type": "object",
                        "description": "the state to observe after the action",
                        "properties": {
                            "element_appears": {
                                "type": "object",
                                "description": "an element that must exist afterwards",
                                "properties": {
                                    "automation_id": {"type": "string"},
                                    "name_contains": {"type": "string"},
                                    "control_type": {"type": "string"},
                                },
                            },
                            "element_vanishes": {
                                "type": "object",
                                "description": "an element that must be gone afterwards",
                                "properties": {
                                    "automation_id": {"type": "string"},
                                    "name_contains": {"type": "string"},
                                    "control_type": {"type": "string"},
                                },
                            },
                            "label_matches": {
                                "type": "object",
                                "description": "a control whose visible label must "
                                               "match this regex afterwards",
                                "properties": {
                                    "automation_id": {"type": "string"},
                                    "name_contains": {"type": "string"},
                                    "regex": {"type": "string"},
                                },
                            },
                        },
                    },
                    "retries": {"type": "integer", "default": 3},
                    "timeout": {"type": "number", "default": 5.0},
                    "interval": {"type": "number", "default": 0.2},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "required": ["hwnd", "expect"],
            },
        ),
        Tool(
            name="native_surfaces",
            description="Report the native ways an application can be driven: "
                        "a command line it ships, a URL scheme it registers, a "
                        "registered executable, or a Store AUMID. These are "
                        "cheaper and more deterministic than driving the "
                        "window, so look here before clicking. Read-only.",
            inputSchema={
                "type": "object",
                "properties": {
                    "app": {"type": "string",
                            "description": "the application display name"},
                    "cli_names": {"type": "array", "items": {"type": "string"},
                                  "description": "CLI names to look for"},
                    "schemes": {"type": "array", "items": {"type": "string"},
                                "description": "URL schemes to look for"},
                    "exe_names": {"type": "array", "items": {"type": "string"},
                                  "description": "App Paths entries to look for"},
                },
                "required": ["app"],
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
            img = capture_window(hwnd) if hwnd else capture_screen()
            img.save(out, "PNG")
            return [TextContent(type="text", text=_to_json(
                {"captured": True, "dry_run": False, "path": out,
                 "window": hwnd, "size": list(img.size)}))]

        if name == "detect_elements":
            hwnd = arguments["hwnd"]
            elems = detect_elements(
                hwnd,
                restore=arguments.get("restore", False),
                max_depth=arguments.get("depth", 30),
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
                guard_hwnd = arguments.get("hwnd")
                if guard_hwnd and not is_foreground_window(guard_hwnd):
                    return [TextContent(type="text", text=_to_json({
                        "error": "focus guard: target window is not foreground",
                        "hwnd": guard_hwnd,
                        "tool": name,
                    }))]
                click_xy(cx, cy, button=arguments.get("button", "left"),
                         double=arguments.get("double", False))
            return [TextContent(type="text", text=_to_json({
                "clicked": not dry_run,
                "center": {"x": cx, "y": cy},
                "target": target.to_dict() if target else None,
            }))]

        if name == "native_surfaces":
            from openeyes.core.native import discover
            app = arguments.get("app")
            if not app:
                return [TextContent(type="text", text=_to_json(
                    {"error": "need app", "tool": name}))]
            try:
                report = discover(
                    app,
                    cli_names=arguments.get("cli_names") or None,
                    schemes=arguments.get("schemes") or None,
                    exe_names=arguments.get("exe_names") or None,
                )
            except Exception as exc:
                return [TextContent(type="text", text=_to_json(
                    {"error": str(exc), "tool": name, "app": app}))]
            return [TextContent(type="text", text=_to_json(report.to_dict()))]

        if name == "click_by_id":
            from openeyes.core.selector import click_element
            hwnd = arguments["hwnd"]
            report = click_element(
                hwnd,
                automation_id=arguments.get("automation_id"),
                control_type=arguments.get("control_type"),
                method=arguments.get("method", "auto"),
                dry_run=arguments.get("dry_run", True),
            )
            return [TextContent(type="text", text=_to_json(report))]

        if name == "act_and_verify":
            from openeyes.core.selector import click_element
            from openeyes.core.verify import act_until

            hwnd = arguments["hwnd"]
            expect = arguments.get("expect") or {}
            dry_run = arguments.get("dry_run", True)
            method = arguments.get("method", "auto")
            automation_id = arguments.get("automation_id")
            control_type = arguments.get("control_type")
            name_regex = arguments.get("name_regex")

            if dry_run:
                # A dry run resolves the target and reports the plan; it must
                # not click and must not claim the action took effect.
                hits = _match_elements(hwnd, {
                    "automation_id": automation_id,
                    "control_type": control_type,
                })
                return [TextContent(type="text", text=_to_json({
                    "ok": False,
                    "dry_run": True,
                    "attempts": 0,
                    "target": hits[0].to_dict() if hits else None,
                    "resolved": bool(hits),
                    "expect": expect,
                    "detail": "would act, then verify the expected state",
                }))]

            state = {"method": method}

            def act():
                report = click_element(
                    hwnd,
                    automation_id=automation_id,
                    name_regex=name_regex,
                    control_type=control_type,
                    method=state["method"],
                    dry_run=False,
                )
                if not report.get("ok") and name_regex:
                    report = click_element(
                        hwnd,
                        name_contains=name_regex,
                        control_type=control_type,
                        method=state["method"],
                        dry_run=False,
                    )
                return report

            def observe():
                return _observe(hwnd, expect)["satisfied"]

            def on_retry(attempt, _result):
                # switch strategy: the pixel fallback is the last resort
                state["method"] = ("pixel" if state["method"] != "pixel"
                                   else "invoke")

            outcome = act_until(act, observe,
                                retries=int(arguments.get("retries", 3)),
                                timeout=float(arguments.get("timeout", 5.0)),
                                interval=float(arguments.get("interval", 0.2)),
                                on_retry=on_retry)
            observed = _observe(hwnd, expect)
            payload = {
                "ok": bool(outcome["ok"]),
                "attempts": outcome["attempts"],
                "result": outcome["result"],
                "elapsed": outcome["elapsed"],
                "error": outcome["error"],
                "observed": observed,
            }
            return [TextContent(type="text", text=_to_json(payload))]

        if name == "grid":
            import win32gui
            hwnd = arguments["hwnd"]
            row = arguments["row"]
            col = arguments["col"]
            rows = arguments.get("rows", 3)
            cols = arguments.get("cols", 3)
            dry_run = arguments.get("dry_run", True)
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            cell_w = (right - left) / cols
            cell_h = (bottom - top) / rows
            cx = int(left + (col - 0.5) * cell_w)
            cy = int(top + (row - 0.5) * cell_h)
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
            backend = arguments.get("backend", "edge")
            if dry_run:
                return [TextContent(type="text", text=_to_json({
                    "launched": False,
                    "dry_run": True,
                    "port": port,
                    "url": url,
                    "profile_dir": profile_dir,
                    "seed": seed,
                    "headless": headless,
                    "backend": backend,
                }))]
            if backend == "lightpanda":
                from openeyes.backends import lightpanda as lp_backend
                info = lp_backend.launch_lightpanda(port=port, url=url)
                info = {**info, "launched": True, "dry_run": False}
                return [TextContent(type="text", text=_to_json(info))]
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
                url_contains = arguments.get("url_contains")
                press_enter = bool(arguments.get("press_enter", False))
                source_text = arguments.get("source_text")
                has_target = any(arguments.get(k) is not None for k in
                                 ("hint", "idx", "name_contains", "control_type"))
                if dry_run and not has_target:
                    target_url = None
                    if url_contains:
                        conn = browser_backend.connect(
                            port=arguments.get("port", 9222),
                            url_contains=url_contains,
                        )
                        target_url = conn.current_url()
                    payload = {
                        "sent": False,
                        "sent_chars": 0,
                        "would_send_chars": len(text),
                        "into": "(focused)",
                        "press_enter": press_enter,
                        "dry_run": True,
                    }
                    if url_contains:
                        payload["target_url"] = target_url
                    return [TextContent(type="text", text=_to_json(payload))]
                conn = browser_backend.connect(
                    port=arguments.get("port", 9222),
                    url_contains=url_contains,
                )
                target_url = conn.current_url() if url_contains else None
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
                    if chosen is None:
                        focused = next(
                            (e for e in browser_backend.scan_dom(conn)
                             if e.state.focused),
                            None,
                        )
                        validate_text_entry(
                            text, focused, source_text=source_text)
                    else:
                        validate_text_entry(
                            text, chosen, source_text=source_text)
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
                    **({"target_url": target_url} if url_contains else {})
                }))]
            except Exception as e:
                return [TextContent(type="text", text=_to_json(
                    {"error": str(e), "tool": name}))]

        if name == "browser_shot":
            try:
                out = arguments["out"]
                url_contains = arguments.get("url_contains")
                target_url = None
                if arguments.get("dry_run", True):
                    if url_contains:
                        conn = browser_backend.connect(
                            port=arguments.get("port", 9222),
                            url_contains=url_contains,
                        )
                        target_url = conn.current_url()
                    payload = {
                        "captured": False,
                        "dry_run": True,
                        "path": out,
                    }
                    if url_contains:
                        payload["target_url"] = target_url
                    return [TextContent(type="text", text=_to_json(payload))]
                conn = browser_backend.connect(
                    port=arguments.get("port", 9222),
                    url_contains=url_contains,
                )
                path = browser_backend.screenshot(conn, out)
                return [TextContent(type="text", text=_to_json({
                    "captured": True,
                    "dry_run": False,
                    "path": path,
                }))]
            except Exception as e:
                return [TextContent(type="text", text=_to_json(
                    {"error": str(e), "tool": name}))]
        if name == "vision_detect":
            from openeyes.backends.vision import elements_from_detections
            raw, err = _load_detections(arguments)
            if err is not None:
                return [TextContent(type="text", text=_to_json(
                    {"error": err, "tool": name}))]
            elems = elements_from_detections(
                raw,
                origin=(arguments.get("origin_x", 0), arguments.get("origin_y", 0)),
                score=arguments.get("score", 0.9),
            )
            elems = elems[: arguments.get("max_results", 200)]
            return [TextContent(type="text", text=_to_json(
                [e.to_dict() for e in elems]))]

        if name == "resolve_elements":
            from openeyes.backends.vision import elements_from_detections
            from openeyes.core.resolve import resolve_elements as resolve_chain
            vision_elements = None
            if arguments.get("detections_path"):
                raw, err = _load_detections(arguments)
                if err is not None:
                    return [TextContent(type="text", text=_to_json(
                        {"error": err, "tool": name}))]
                vision_elements = elements_from_detections(
                    raw,
                    origin=(arguments.get("origin_x", 0), arguments.get("origin_y", 0)),
                )
            result = resolve_chain(
                hwnd=arguments.get("hwnd") or None,
                port=arguments.get("port", 9222) or None,
                url_contains=arguments.get("url_contains"),
                vision_elements=vision_elements,
            )
            payload = result.to_dict()
            payload["elements"] = payload["elements"][: arguments.get("max_results", 200)]
            return [TextContent(type="text", text=_to_json(payload))]

        if name == "app_ensure":
            from openeyes.core.lifecycle import ensure_app
            result = ensure_app(
                title_contains=arguments.get("title_contains"),
                class_name=arguments.get("class_name"),
                process_name=arguments.get("process_name"),
                exe=arguments.get("exe"),
                aumid=arguments.get("aumid"),
                timeout=float(arguments.get("timeout", 30.0)),
                dry_run=bool(arguments.get("dry_run", True)),
            )
            payload = result.to_dict()
            payload["dry_run"] = bool(arguments.get("dry_run", True))
            return [TextContent(type="text", text=_to_json(payload))]

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
