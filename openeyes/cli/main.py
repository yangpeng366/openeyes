"""`eyes` CLI entrypoint.

Usage:
    eyes windows list [--title-contains <str>] [--class-name <str>] [--regex <re>]
    eyes capture --window <hwnd>|--screen --out <png> [--json]
    eyes detect --window <hwnd> [--restore] [--pretty] [--name-contains <str>]
                [--control-type <str>] [--regex <re>]
    eyes click  --window <hwnd> [--name-contains <str>] [--control-type <str>]
                [--regex <re>] [--x N --y N] [--dry-run|--go]
                [--button left|right|middle] [--double] [--return-to-origin]
                [--method auto|invoke|message|pixel|foreground] [--budget N]
    eyes grid   --window <hwnd> --row R --col C [--rows N --cols N] [--dry-run|--go]
    eyes hotkey [--combo ctrl+a]      # or: eyes send hotkey --combo ctrl+a
    eyes type   --text <str> [--interval N]
    eyes record replay <trajectory.jsonl> [--json]
    eyes record export <trajectory.jsonl> --skill-dir <dir> [--description TEXT] [--force] [--json]
    eyes version

Default click behavior is **dry-run**. Pass --go to actually click.
The JSON output says what the click cost: method, physical_input,
focus_stolen, cursor_moved. The rungs that reach for your pointer are
paid for from --budget (0 = never, the default).
"""
from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING

import click

from openeyes import __version__
from openeyes.cli.output import emit_error, emit_json, render_table, wants_json
from openeyes.core.actuator import click_xy, send_hotkey, type_text
from openeyes.core.capture import capture_screen, capture_window
from openeyes.core.coords import to_screen_point
from openeyes.core.selector import detect_elements, find_elements
from openeyes.core.windows import find_window, foreground_window

if TYPE_CHECKING:
    from openeyes.core.schema import Element


def json_option(f):
    """Add the global --json switch to a command.

    The group declares it, and Click hands the value down through
    ``ctx.obj``; this decorator lets a subcommand also accept it after
    its own name, so neither call order surprises a caller.
    """
    return click.option(
        "--json", "as_json", is_flag=True, default=False,
        help="machine-readable JSON output",
    )(f)


@click.group()
@click.version_option(version=__version__, prog_name="eyes")
@click.option("--json", "as_json", is_flag=True,
              help="machine-readable JSON output (works before or after "
                   "the subcommand)")
@click.pass_context
def cli(ctx: click.Context, as_json: bool) -> None:
    """OpenEyes — AI-friendly computer-use primitives."""
    # A global --json: `eyes --json detect ...` and `eyes detect ... --json`
    # must both work, so the flag is propagated to every subcommand.
    ctx.ensure_object(dict)
    ctx.obj["as_json"] = as_json


# ----- windows --------------------------------------------------------------

@cli.group()
def windows() -> None:
    """List / find top-level windows."""


@windows.command("list")
@click.option("--title-contains")
@click.option("--class-name")
@click.option("--regex")
@json_option
def windows_list(title_contains: str | None, class_name: str | None,
                 regex: str | None, as_json: bool) -> None:
    """Enumerate visible top-level windows."""
    try:
        wins = find_window(title_contains=title_contains,
                           class_name=class_name, regex=regex)
    except Exception as exc:
        emit_error(f"could not enumerate windows: {exc}", code=4)
        return
    payload = {"ok": True, "count": len(wins),
               "windows": [w.to_dict() for w in wins]}
    if as_json or wants_json():
        emit_json(payload)
        return
    if not wins:
        click.echo("  (no matching window)")
        return
    render_table(
        ["hwnd", "title", "class", "pid", "x", "y", "w", "h"],
        [[w.hwnd, w.title, w.class_name, w.pid, w.x, w.y, w.w, w.h]
         for w in wins],
    )


# ----- capture --------------------------------------------------------------

@cli.command()
@click.option("--window", type=int, default=0, help="hwnd to capture (0 = full screen)")
@click.option("--out", required=True, help="output PNG path")
@click.option("--json", "as_json", is_flag=True, help="write metadata JSON next to image")
def capture(window: int, out: str, as_json: bool) -> None:
    """Capture a window or the full screen to PNG."""
    try:
        img = capture_window(window) if window else capture_screen()
    except Exception as exc:
        emit_error(
            f"could not capture hwnd={window}: {exc}",
            code=4, hwnd=window,
            hint="run `eyes windows list` to get a real hwnd",
        )
        return
    img.save(out, "PNG")
    click.echo(out)
    if as_json:
        meta = {"path": out, "window": window, "size": list(img.size)}
        with open(out + ".json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)


# ----- detect ---------------------------------------------------------------

@cli.command()
@click.option("--window", type=int, required=True)
@click.option("--restore", is_flag=True, help="call ShowWindow(SW_RESTORE) first (UWP)")
@click.option("--depth", type=int, default=30, show_default=True,
              help="how deep to walk the accessibility tree (a web "
                   "surface buries its DOM around depth 20)")
@click.option("--automation-id", default=None,
              help="keep only the control with this stable id")
@click.option("--name-contains")
@click.option("--control-type")
@click.option("--regex")
@click.option("--max", type=int, default=200)
@click.option("--pretty", is_flag=True)
@json_option
def detect(window: int, restore: bool, depth: int,
           name_contains: str | None, control_type: str | None, regex: str | None,
           automation_id: str | None, max: int, pretty: bool,
           as_json: bool) -> None:
    """Enumerate interactive elements in a window."""
    try:
        elems = detect_elements(window, restore=restore, max_depth=depth)
    except BaseException as e:
        emit_error(f"detect failed for hwnd={window}: {str(e) or repr(e)}",
                   code=4, hwnd=window)
        return
    elems = find_elements(elems, name_contains=name_contains,
                          control_type=control_type, regex=regex,
                          automation_id=automation_id)
    elems = elems[:max]
    if as_json or wants_json():
        emit_json({"ok": True, "hwnd": window, "count": len(elems),
                   "elements": [e.to_dict() for e in elems]})
        return
    if pretty:
        for e in elems:
            click.echo(
                f"[{e.control_type:>14}] ({e.center.x:>4},{e.center.y:>4}) "
                f"{e.bbox.w}x{e.bbox.h}  '{e.name}'"
            )
    else:
        emit_json([e.to_dict() for e in elems])


# ----- status ---------------------------------------------------------------

@cli.command()
@click.option("--window", type=int, required=True)
@click.option("--restore", is_flag=True)
@click.option("--max", type=int, default=30)
@json_option
def status(window: int, restore: bool, max: int, as_json: bool) -> None:
    """Quick snapshot of the most likely-interactive elements in a window.

    Useful for live debugging: shows buttons with names that contain any of
    known action verbs (Click, Reset, Reconnect, Secure, Submit, OK, Cancel,
    Save, etc.) plus their current center coordinates.
    """
    ACTION_VERBS = (
        "click", "reset", "reconnect", "secure", "submit", "ok", "cancel",
        "save", "apply", "close", "send", "open", "new", "delete", "remove",
        "add", "edit", "confirm", "back", "next", "continue", "finish",
        "yes", "no", "accept", "reject", "approve", "deny", "upgrade",
        "connect", "disconnect", "sign", "log", "upload", "download",
        "browse", "search", "find", "filter", "refresh", "reload",
    )
    try:
        elems = detect_elements(window, restore=restore)
    except Exception as exc:
        emit_error(f"status failed for hwnd={window}: {exc}", code=4,
                   hwnd=window)
        return
    seen = set()
    hits = []
    for e in elems:
        name_l = (e.name or "").lower()
        if not name_l or e.control_type != "Button":
            continue
        if not any(v in name_l for v in ACTION_VERBS):
            continue
        # dedupe by (name, center)
        key = (e.name, e.center.x, e.center.y)
        if key in seen:
            continue
        seen.add(key)
        hits.append(e)
        if len(hits) >= max:
            break
    if as_json or wants_json():
        emit_json({"ok": True, "hwnd": window, "total_elements": len(elems),
                   "count": len(hits),
                   "buttons": [{"name": e.name, "center": {"x": e.center.x,
                                                           "y": e.center.y},
                                "w": e.bbox.w, "h": e.bbox.h,
                                "automation_id": e.automation_id}
                               for e in hits]})
        return
    click.echo(f"[status] hwnd={window} total_elements={len(elems)}")
    if not hits:
        click.echo("  (no action-verb buttons found)")
        return
    render_table(["center", "size", "name", "automation_id"],
                 [[f"({e.center.x},{e.center.y})", f"{e.bbox.w}x{e.bbox.h}",
                   e.name, e.automation_id] for e in hits])


# ----- click ----------------------------------------------------------------

def _click_by_ladder(window: int, target: Element | None, desc: str,
                     method: str, budget: int, as_json: bool) -> None:
    """Deliver the click through the application, not the pointer.

    The ladder walks ``invoke -> message -> pixel``. The rungs that reach
    for the user's pointer are paid for from the disturbance budget: with
    no budget the action fails with a structured error instead of taking
    the mouse. A target that cannot be reached at all is a failure, never
    a quiet downgrade to something else.
    """
    from openeyes.agent.profile import FocusBudget
    from openeyes.core.selector import click_element

    if target is None:
        raise click.UsageError(
            f"--method {method} needs a selector target: --name-contains / "
            "--control-type / --regex")
    if not (target.automation_id or target.name):
        raise click.UsageError(
            f"--method {method} cannot address this target: it has neither a "
            "name nor an automation id")
    try:
        report = click_element(window,
                               automation_id=target.automation_id or None,
                               name_contains=target.name or None,
                               control_type=target.control_type or None,
                               method=method, dry_run=False,
                               budget=FocusBudget(budget))
    except Exception as exc:
        emit_error(f"could not act on {desc}: {exc}", code=4, hwnd=window)
        return
    if not report.get("ok"):
        code = report.get("error_code")
        emit_error(f"{report.get('error') or 'the action was refused'}"
                   + (f" [{code}]" if code else ""),
                   code=3, hwnd=window, error_code=code,
                   method=report.get("method"))
        return
    if as_json or wants_json():
        emit_json({"ok": True, "clicked": True, "dry_run": False,
                   "center": {"x": target.center.x, "y": target.center.y},
                   "description": desc, "target": target.to_dict(),
                   "method": report.get("method"),
                   "physical_input": bool(report.get("physical_input")),
                   "focus_stolen": bool(report.get("focus_stolen")),
                   "cursor_moved": bool(report.get("cursor_moved"))})
        return
    click.echo(f"clicked {desc} ({report.get('method')})")


def _window_origin(hwnd: int) -> tuple[int, int]:
    """The window's top-left corner in absolute screen pixels.

    The UIA and vision backends measure *inside* the window (``uia.py``
    subtracts the window origin while it walks the tree), while
    ``click_xy`` speaks absolute screen pixels. Reading the origin is the
    only way back across the gap, and it is read here - at the click -
    rather than cached from a capture that may be stale by now.
    """
    import win32gui
    left, top, _right, _bottom = win32gui.GetWindowRect(hwnd)
    return int(left), int(top)


@cli.command("click")
@click.option("--window", type=int, default=0)
@click.option("--x", type=int,
              help="absolute screen X - not window-relative")
@click.option("--y", type=int,
              help="absolute screen Y - not window-relative")
@click.option("--name-contains")
@click.option("--control-type")
@click.option("--regex")
@click.option("--button", type=click.Choice(["left", "right", "middle"]), default="left")
@click.option("--double", is_flag=True)
@click.option("--return-to-origin", is_flag=True)
@click.option("--focus-titlebar", "focus_titlebar", is_flag=True,
              help="click title bar first to ensure window focus (UWP-safe). "
                   "Only applies to a physical click.")
@click.option("--method", "method",
              type=click.Choice(["auto", "invoke", "message", "pixel",
                                 "foreground"]),
              default="auto", show_default=True,
              help="how the click is delivered: auto (the default) walks the "
                   "ladder invoke -> message -> pixel and stops at the first "
                   "rung that works; invoke / message ask the application to "
                   "act, so the pointer never moves; pixel says the physical "
                   "click out loud; foreground takes the window to the front "
                   "first")
@click.option("--budget", "budget", type=int, default=0, show_default=True,
              help="how many times this run may take the foreground away from "
                   "you (0 = never, the default). The rungs that reach for "
                   "your pointer are paid for from it.")
@click.option("--dry-run", "dry_run", is_flag=True,
              help="print the plan and do not click (the default); "
                   "accepted so scripts can say it out loud")
@click.option("--go", "go", is_flag=True,
              help="actually click. Default is dry-run.")
@json_option
def tap(window: int, x: int | None, y: int | None,
          name_contains: str | None, control_type: str | None, regex: str | None,
          button: str, double: bool, return_to_origin: bool,
          focus_titlebar: bool, method: str, budget: int, go: bool, dry_run: bool,
          as_json: bool) -> None:
    """Click by coordinates OR by element selector.

    By default the click is dry-run — only the resolved target is printed.
    Pass --go to actually click.

    Two coordinate spaces meet here, and they are not interchangeable:

    * ``--x/--y`` are **absolute screen pixels**. They are used verbatim
      and are never shifted by the window origin.
    * a selector's centre comes from a backend that measures *inside* the
      window (uia / vision: window-relative, cdp: viewport-relative). A
      physical click on such a target is converted with
      ``openeyes.core.coords.to_screen_point`` — the window origin is
      added back before the pointer moves, so the click lands where the
      element is rather than one window origin away from it.

    A pixel click therefore reports both ``center`` (the element's own
    coordinates, in its backend's space) and ``screen_point`` (the
    absolute point the pointer was sent to).

    ``--method`` says how the click reaches the application:

    * ``auto`` (default) - walk the ladder ``invoke -> message -> pixel``
      and stop at the first rung that works.
    * ``invoke`` - the application performs the click itself. The pointer
      never moves and the window need not be in the foreground.
    * ``message`` - a window message to the control. The pointer stays
      where it is.
    * ``pixel`` - a physical click at the element centre.
    * ``foreground`` - take the window to the front, then click.

    The rungs that reach for the user's pointer are paid for from
    ``--budget``: how many times this run may take the foreground away
    from you. The default is 0 - never - so a control with no semantic
    path fails with a structured error instead of taking your mouse. Pass
    ``--budget N`` to allow N such clicks, or ``--method pixel`` to ask
    for one explicitly.

    The JSON output always states what the click cost: ``method``,
    ``physical_input`` (the user's pointer was driven), ``cursor_moved``
    and ``focus_stolen`` (the focus was pulled onto the target).
    """
    go = go and not dry_run
    target = None
    if x is not None and y is not None:
        cx, cy = x, y
        desc = f"@ ({cx},{cy})"
    elif name_contains or control_type or regex:
        if not window:
            raise click.UsageError("--window is required for selector-based click")
        try:
            elems = detect_elements(window, restore=True)
        except Exception as exc:
            emit_error(f"could not read the tree of hwnd={window}: {exc}",
                       code=4, hwnd=window)
            return
        matches = find_elements(elems, name_contains=name_contains,
                                control_type=control_type, regex=regex)
        if not matches:
            emit_error(
                "no element matched "
                f"name_contains={name_contains!r} "
                f"control_type={control_type!r} regex={regex!r} "
                f"in hwnd={window}",
                code=3, hwnd=window,
            )
            return
        target = matches[0]
        cx, cy = target.center.x, target.center.y
        desc = (f"@ ({cx},{cy})  name={target.name!r} "
                f"control_type={target.control_type!r}")
    else:
        raise click.UsageError("need --x/--y OR --name-contains/--control-type/--regex")

    if not go:
        if as_json or wants_json():
            emit_json({"ok": True, "clicked": False, "dry_run": True,
                       "center": {"x": cx, "y": cy}, "description": desc,
                       "target": target.to_dict() if target else None,
                       "method": method, "physical_input": False,
                       "focus_stolen": False, "cursor_moved": False})
            return
        click.echo(f"[dry-run] would click {desc} ({method})")
        return

    # A right click or a double click is a physical gesture: no semantic
    # path delivers one.
    semantic = method in ("invoke", "message", "foreground")
    if semantic and target is None:
        raise click.UsageError(
            f"--method {method} needs a selector target: --name-contains / "
            "--control-type / --regex")
    if semantic and (button != "left" or double):
        raise click.UsageError(
            f"--method {method} cannot deliver a {button} "
            f"{'double ' if double else ''}click")
    if target is not None and method != "pixel":
        _click_by_ladder(window, target, desc, method, budget, as_json)
        return

    if focus_titlebar and window:
        from openeyes.actuators.win32 import focus_window
        ok = focus_window(window)
        click.echo(f"focus_window: {ok}", err=True)
        if not ok:
            click.echo("focus guard: target window is not foreground; click aborted", err=True)
            sys.exit(4)

    if return_to_origin:
        import win32api
        ox, oy = win32api.GetCursorPos()
    # A selector was measured in a window-relative space (uia / vision) or a
    # viewport-relative one (cdp); a physical click needs absolute screen
    # pixels. --x/--y is already absolute and is used verbatim.
    screen_point: tuple[int, int] | None = None
    if target is not None:
        try:
            window_origin = _window_origin(window)
        except Exception as exc:
            emit_error(f"could not read the rectangle of hwnd={window}: {exc}",
                       code=4, hwnd=window)
            return
        try:
            screen_point = to_screen_point(target, window_origin=window_origin)
        except ValueError as exc:
            emit_error(
                f"could not convert {desc} to screen coordinates: {exc}",
                code=4, hwnd=window,
            )
            return
    click_x, click_y = screen_point if screen_point is not None else (cx, cy)
    foreground_before = foreground_window()
    click_xy(click_x, click_y, button=button, double=double)
    if return_to_origin:
        win32api.SetCursorPos((ox, oy))
    # The focus was stolen when it came onto the target from somewhere else.
    focus_stolen = bool(window) and foreground_before != window \
        and foreground_window() == window
    if as_json or wants_json():
        emit_json({"ok": True, "clicked": True, "dry_run": False,
                   "center": {"x": cx, "y": cy},
                   "screen_point": {"x": click_x, "y": click_y},
                   "description": desc,
                   "target": target.to_dict() if target else None,
                   "method": "pixel", "physical_input": True,
                   "focus_stolen": focus_stolen, "cursor_moved": True})
        return
    click.echo(f"clicked {desc}" if screen_point is None
               else f"clicked {desc} at screen ({click_x},{click_y})")


# ----- grid -----------------------------------------------------------------

@cli.command()
@click.option("--window", type=int, required=True)
@click.option("--row", type=int, required=True, help="1-based row index")
@click.option("--col", type=int, required=True, help="1-based column index")
@click.option("--rows", type=int, default=3, help="number of rows (default 3)")
@click.option("--cols", type=int, default=3, help="number of columns (default 3)")
@click.option("--dry-run/--go", default=True)
def grid(window: int, row: int, col: int, rows: int, cols: int, dry_run: bool) -> None:
    """Click the center of a grid cell overlaid on the window (Vimium-style)."""
    import win32gui
    left, top, right, bottom = win32gui.GetWindowRect(window)
    cell_w = (right - left) / cols
    cell_h = (bottom - top) / rows
    cx = int(left + (col - 0.5) * cell_w)
    cy = int(top + (row - 0.5) * cell_h)
    if dry_run:
        click.echo(f"[dry-run] grid ({row},{col}) of {rows}x{cols} @ ({cx},{cy})")
        return
    click_xy(cx, cy)
    click.echo(f"clicked grid ({row},{col}) @ ({cx},{cy})")


# ----- hotkey / type --------------------------------------------------------

@cli.command()
@click.option("--combo", required=True, help="e.g. ctrl+a, alt+F4")
@json_option
def hotkey(combo: str, as_json: bool) -> None:
    """Press a hotkey chord, e.g. eyes hotkey --combo ctrl+a."""
    keys = [k.strip() for k in combo.split("+") if k.strip()]
    send_hotkey(*keys)
    if as_json:
        emit_json({"ok": True, "combo": combo})
        return
    click.echo(f"sent hotkey {combo}")


@cli.command()
@click.option("--text", required=True)
@click.option("--interval", type=float, default=0.0)
@json_option
def type(text: str, interval: float, as_json: bool) -> None:
    """Type a literal string."""
    try:
        type_text(text, interval=interval)
    except ValueError as exc:
        emit_error(str(exc), code=4, hint="use clipboard paste for non-ASCII text")
        return
    if as_json:
        emit_json({"ok": True, "typed_chars": len(text)})
        return
    click.echo(f"typed {len(text)} chars")


# ----- browser (CDP) -------------------------------------------------------

@cli.group()
def record() -> None:
    """Inspect, replay and export recorded trajectories."""


@record.command("replay")
@click.argument("trajectory_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="emit the replay plan as JSON")
def record_replay(trajectory_path: str, as_json: bool) -> None:
    """Preview replay actions without sending input."""
    from openeyes.record import Trajectory
    from openeyes.record.replayer import Replayer

    trajectory = Trajectory.load(trajectory_path)
    report = Replayer(dry_run=True).replay(trajectory)
    if as_json:
        click.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return

    click.echo(f"[dry-run] trajectory {trajectory.name} ({trajectory.id[:8]})")
    for item in report.actions:
        plan = item["plan"]
        detail = plan.get("selector") or plan.get("combo") or plan.get("text") or ""
        click.echo(f"{item['index']}: {plan['kind']} {detail}".rstrip())
    click.echo(
        f"{len(report.actions)} planned, {len(report.skipped)} skipped, "
        f"{len(report.errors)} errors"
    )


@record.command("export")
@click.argument("trajectory_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--skill-dir", "skill_dir", required=True, type=click.Path(file_okay=False),
              help="directory that receives SKILL.md and trajectory.jsonl")
@click.option("--description", default="",
              help="one-line description for the skill front-matter")
@click.option("--force", is_flag=True,
              help="overwrite SKILL.md and trajectory.jsonl in a non-empty directory")
@json_option
def record_export(trajectory_path: str, skill_dir: str, description: str,
                  force: bool, as_json: bool) -> None:
    """Package a recording as a loadable skill directory.

    Writes <skill_dir>/SKILL.md (from to_codex_skill) and copies the source
    trajectory byte-for-byte to <skill_dir>/trajectory.jsonl. Replay of the
    exported folder is a dry-run: `eyes record replay ... --json` plans the
    steps and never sends input.
    """
    from openeyes.record.export.skill_package import SkillExportError, export_skill_dir

    try:
        export = export_skill_dir(
            trajectory_path, skill_dir, description=description, force=force,
        )
    except SkillExportError as exc:
        emit_error(str(exc), code=2,
                   hint="pass --force to overwrite the two exported files")
        return

    if as_json:
        emit_json(export.to_dict())
        return

    click.echo(f"skill {export.name} -> {export.skill_md}")
    click.echo(f"trajectory {export.trajectory} ({export.steps} steps)")
    click.echo(f'[dry-run] eyes record replay "{export.trajectory}" --json')


@cli.group()
def browser() -> None:
    """Drive a browser over CDP (Edge / Chrome) or Firefox Remote Agent (BiDi).

    Subcommands:

    \b
        eyes browser launch  --url <url>
        eyes browser tabs
        eyes browser scan    [--pretty]
        eyes browser click   --hint <a|aa> ...
        eyes browser type    --text <str>
        eyes browser shot    --out <png>

    Defaults are dry-run unless ``--go`` is passed.
    """


def _protocol_option(func):
    """Add the --protocol switch shared by every browser subcommand."""
    return click.option(
        "--protocol", type=click.Choice(["cdp", "bidi"]), default="cdp",
        show_default=True,
        help="cdp = Chromium/Edge DevTools; bidi = Firefox Remote Agent",
    )(func)


def _host_header_option(func):
    """Add the optional HTTP Host override used by forwarded Firefox."""
    return click.option(
        "--host-header", default=None,
        help="BiDi HTTP Host header (for SSH forwarding, e.g. localhost:9222)",
    )(func)


def _browser_connection_options(protocol: str,
                                host_header: str | None) -> dict[str, str]:
    if protocol == "bidi" and host_header:
        return {"host_header": host_header}
    return {}


def _backend(protocol: str):
    """Resolve a browser protocol name to its backend module."""
    from openeyes.backends import browser as browser_api
    return browser_api.backend_for(protocol)


@browser.command("launch")
@click.option("--url", default="about:blank", show_default=True)
@click.option("--port", type=int, default=9222, show_default=True)
@click.option("--profile-dir", default=None,
              help="reuse a temp profile dir to keep cookies across launches")
@click.option("--seed/--no-seed", default=True,
              help="copy session slices from live Edge profile (Default)")
@click.option("--headless", is_flag=True)
@click.option("--backend", type=click.Choice(["edge", "lightpanda"]),
              default="edge", show_default=True,
              help="edge = real Edge (default, persistent cookies); "
                   "lightpanda = WSL2 + Docker lightpanda/browser:nightly "
                   "(~123 MB RAM, no persistent profile, no screenshot).")
def browser_launch(url: str, port: int, profile_dir: str | None,
                   seed: bool, headless: bool, backend: str) -> None:
    """Launch a Chromium-family browser with --remote-debugging-port=PORT."""
    if backend == "lightpanda":
        from openeyes.backends import lightpanda as browser_backend
        info = browser_backend.launch_lightpanda(port=port, url=url)
        click.echo(json.dumps(info, ensure_ascii=False, indent=2))
        return
    from openeyes.backends import cdp as browser_backend
    prof = browser_backend.Path(profile_dir) if profile_dir else None
    info = browser_backend.launch_edge(
        port=port, url=url, profile_dir=prof, seed=seed, headless=headless,
    )
    click.echo(json.dumps(info, ensure_ascii=False, indent=2))


@browser.command("tabs")
@click.option("--port", type=int, default=9222, show_default=True)
@_protocol_option
@_host_header_option
@json_option
def browser_tabs(port: int, protocol: str, host_header: str | None,
                 as_json: bool) -> None:
    """List page targets on the configured port (CDP or Firefox BiDi)."""
    browser_backend = _backend(protocol)
    try:
        pages = browser_backend.list_tabs(
            port, **_browser_connection_options(protocol, host_header))
    except Exception as e:
        emit_error(f"failed to reach :{port}: {e}", code=4, port=port)
        return
    short = [{"id": p.get("id"), "title": p.get("title"),
              "url": p.get("url"), "type": p.get("type")} for p in pages]
    if as_json or wants_json():
        emit_json({"ok": True, "port": port, "count": len(short),
                   "tabs": short})
        return
    if not short:
        click.echo("  (no page target)")
        return
    render_table(["id", "type", "title", "url"],
                 [[t["id"], t["type"], t["title"], t["url"]] for t in short])


@browser.command("scan")
@click.option("--port", type=int, default=9222, show_default=True)
@_protocol_option
@_host_header_option
@click.option("--url", default=None, help="navigate first (default: skip)")
@click.option("--url-contains", default=None)
@click.option("--name-contains")
@click.option("--control-type")
@click.option("--regex")
@click.option("--max", type=int, default=200)
@click.option("--pretty", is_flag=True)
@click.option("--no-hints", is_flag=True,
              help="skip letter-hint assignment (faster, no annotations)")
def browser_scan(port: int, protocol: str, host_header: str | None,
                  url: str | None, url_contains: str | None,
                  name_contains: str | None, control_type: str | None,
                  regex: str | None, max: int, pretty: bool,
                  no_hints: bool) -> None:
    """DOM probe the active page; return interactive elements as JSON."""
    browser_backend = _backend(protocol)
    from openeyes.core.hints import assign_hints
    from openeyes.core.selector import find_elements
    conn = browser_backend.connect(
        port=port, url_contains=url_contains,
        **_browser_connection_options(protocol, host_header),
    )
    if url:
        conn.navigate(url)
        time.sleep(0.6)
    elems = browser_backend.scan_dom(conn)
    if not no_hints:
        assign_hints(elems)
    elems = find_elements(elems, name_contains=name_contains,
                           control_type=control_type, regex=regex)
    elems = elems[:max]
    if pretty:
        page = conn.current_url() or "(no url)"
        title = conn.page_title() or "(untitled)"
        click.echo(f"# {title}  <{page}>  [{len(elems)} interactive]")
        for e in elems:
            tag = f"[{e.hint}]" if e.hint else "     "
            click.echo(
                f"  {tag} [{e.control_type:>12}] "
                f"({e.center.x:>4},{e.center.y:>4}) "
                f"{e.bbox.w}x{e.bbox.h}  {e.name!r}"
            )
    else:
        click.echo(json.dumps([e.to_dict() for e in elems],
                               ensure_ascii=False, indent=2))


def _resolve_browser_target(conn, port: int, protocol: str,
                              host_header: str | None, hint: str | None,
                              idx: int | None, name_contains: str | None,
                              control_type: str | None,
                              url_contains: str | None):
    """Pick one element out of a fresh DOM scan (click/type entry-point)."""
    browser_backend = _backend(protocol)
    from openeyes.core.hints import assign_hints, find_by_hint
    from openeyes.core.selector import find_elements
    if conn is None:
        conn = browser_backend.connect(
            port=port, url_contains=url_contains,
            **_browser_connection_options(protocol, host_header),
        )
    elems = browser_backend.scan_dom(conn)
    assign_hints(elems)
    elems = find_elements(elems, name_contains=name_contains,
                           control_type=control_type)
    if not elems:
        raise click.UsageError("no matching element")
    chosen = None
    if hint:
        chosen = find_by_hint(elems, hint)
        if not chosen:
            raise click.UsageError(f"no element with hint {hint!r}")
    elif idx is not None:
        if idx < 0 or idx >= len(elems):
            raise click.UsageError(
                f"--idx {idx} out of range (0..{len(elems) - 1})")
        chosen = elems[idx]
    else:
        chosen = elems[0]
    return chosen, conn


@browser.command("click")
@click.option("--port", type=int, default=9222, show_default=True)
@_protocol_option
@_host_header_option
@click.option("--hint", default=None,
              help="Vimium-style letter (a, s, .. zz, aaa ..)")
@click.option("--idx", type=int, default=None,
              help="0-based index into the scan-ordered list")
@click.option("--name-contains", default=None)
@click.option("--control-type", default=None)
@click.option("--url-contains", default=None)
@click.option("--go", "go", is_flag=True, help="actually click")
@json_option
def browser_click(port: int, protocol: str, host_header: str | None,
                   hint: str | None, idx: int | None, name_contains: str | None,
                   control_type: str | None, url_contains: str | None,
                   go: bool, as_json: bool) -> None:
    """Click a single element resolved by hint/idx/name-contains."""
    browser_backend = _backend(protocol)
    try:
        chosen, conn = _resolve_browser_target(
            None, port, protocol, host_header, hint, idx, name_contains,
            control_type, url_contains)
    except Exception as exc:
        emit_error(f"could not resolve a browser target on port {port}: {exc}",
                   code=4, port=port)
        return
    desc = (f"[{protocol}] '{chosen.name}' ({chosen.control_type}) "
            f"center=({chosen.center.x},{chosen.center.y})")
    if not go:
        if as_json or wants_json():
            emit_json({"ok": True, "clicked": False, "dry_run": True,
                       "center": {"x": chosen.center.x, "y": chosen.center.y},
                       "description": desc,
                       "target": chosen.to_dict()})
            return
        click.echo(f"[dry-run] would click {desc}")
        return
    browser_backend.click_center(conn, chosen)
    if as_json or wants_json():
        emit_json({"ok": True, "clicked": True, "dry_run": False,
                   "center": {"x": chosen.center.x, "y": chosen.center.y},
                   "description": desc, "target": chosen.to_dict()})
        return
    click.echo(f"clicked {desc}")


@browser.command("type")
@click.option("--port", type=int, default=9222, show_default=True)
@_protocol_option
@_host_header_option
@click.option("--text", required=True)
@click.option("--hint", default=None)
@click.option("--idx", type=int, default=None)
@click.option("--name-contains", default=None)
@click.option("--control-type", default=None)
@click.option("--enter/--no-enter", default=False,
              help="press Enter after typing")
@click.option("--source-text", default=None,
              help="user text that authorizes --text (agent safety guard)")
@click.option("--url-contains", default=None)
def browser_type(port: int, protocol: str, host_header: str | None,
                  text: str, hint: str | None, idx: int | None,
                  name_contains: str | None,
                  control_type: str | None, enter: bool, source_text: str | None,
                  url_contains: str | None) -> None:
    """Type text into a focused element (or specify target by hint/idx)."""
    browser_backend = _backend(protocol)
    from openeyes.core.safety import validate_text_entry
    chosen = None
    if hint or idx is not None or name_contains or control_type:
        chosen, conn = _resolve_browser_target(
            None, port, protocol, host_header, hint, idx, name_contains,
            control_type, url_contains)
    else:
        conn = browser_backend.connect(
            port=port, url_contains=url_contains,
            **_browser_connection_options(protocol, host_header),
        )
    if chosen is not None:
        validate_text_entry(text, chosen, source_text=source_text)
    else:
        focused = next(
            (e for e in browser_backend.scan_dom(conn) if e.state.focused),
            None,
        )
        validate_text_entry(text, focused, source_text=source_text)
    browser_backend.type_text(conn, text, element=chosen, press_enter=enter)
    desc = (f"typed {len(text)} chars"
            + (f" into {chosen.name!r}" if chosen else " (focused field)")
            + (" + Enter" if enter else ""))
    click.echo(desc)


@browser.command("shot")
@click.option("--port", type=int, default=9222, show_default=True)
@_protocol_option
@_host_header_option
@click.option("--out", required=True, help="output PNG path")
def browser_shot(port: int, protocol: str, host_header: str | None,
                out: str) -> None:
    """Capture the page viewport as PNG."""
    browser_backend = _backend(protocol)
    conn = browser_backend.connect(
        port=port, **_browser_connection_options(protocol, host_header),
    )
    p = browser_backend.screenshot(conn, out)
    click.echo(p)


# ----- vision ---------------------------------------------------------------

@cli.command("vision")
@click.option("--detections", required=True,
              help="OmniParser-style detection JSON to convert into elements")
@click.option("--origin-x", type=int, default=0, show_default=True,
              help="window top-left inside the image, X (subtracted; pass the "
                   "window origin for a whole-screen capture)")
@click.option("--origin-y", type=int, default=0, show_default=True,
              help="window top-left inside the image, Y (subtracted; pass the "
                   "window origin for a whole-screen capture)")
@click.option("--score", type=float, default=0.9, show_default=True,
              help="confidence for detections that carry none")
@click.option("--pretty", is_flag=True)
def vision(detections: str, origin_x: int, origin_y: int, score: float,
           pretty: bool) -> None:
    """Convert screen-parsing detections into openeyes elements (no model)."""
    from openeyes.backends.vision import elements_from_detections
    with open(detections, encoding="utf-8-sig") as detections_file:
        raw = json.loads(detections_file.read())
    elems = elements_from_detections(raw, origin=(origin_x, origin_y), score=score)
    if pretty:
        for e in elems:
            click.echo(
                f"[{e.control_type:>14}] ({e.center.x:>4},{e.center.y:>4}) "
                f"{e.bbox.w}x{e.bbox.h}  '{e.name}'  score={e.score:.2f}"
            )
    else:
        click.echo(json.dumps([e.to_dict() for e in elems], ensure_ascii=False, indent=2))


# ----- resolve --------------------------------------------------------------

@cli.command("resolve")
@click.option("--window", type=int, default=0,
              help="hwnd to resolve (0 = skip the uia tier)")
@click.option("--port", type=int, default=9222, show_default=True,
              help="CDP port (0 = skip the cdp tier)")
@click.option("--url-contains", default=None)
@click.option("--vision-detections", default=None,
              help="detection JSON feeding the vision tier")
@click.option("--max", type=int, default=200)
@click.option("--pretty", is_flag=True)
@json_option
def resolve(window: int, port: int, url_contains: str | None,
            vision_detections: str | None, max: int, pretty: bool,
            as_json: bool) -> None:
    """Walk the backend chain (uia -> cdp -> vision) and report who answered."""
    from openeyes.core.resolve import resolve_elements
    vision_elements = None
    if vision_detections:
        from openeyes.backends.vision import elements_from_detections
        with open(vision_detections, encoding="utf-8-sig") as detections_file:
            raw = json.loads(detections_file.read())
        vision_elements = elements_from_detections(raw)
    result = resolve_elements(
        hwnd=window or None,
        port=port or None,
        url_contains=url_contains,
        vision_elements=vision_elements,
    )
    elements = result.elements[:max]
    payload = result.to_dict()
    payload["elements"] = payload["elements"][:max]
    if as_json or wants_json():
        emit_json(payload)
        return
    if pretty:
        click.echo(f"used_backend: {result.used_backend or '(none)'}")
        for a in result.attempts:
            mark = "ok " if a.ok else "skip"
            click.echo(f"  [{mark}] {a.backend:<7} count={a.count} {a.reason}")
        for e in elements:
            click.echo(
                f"[{e.control_type:>14}] ({e.center.x:>4},{e.center.y:>4}) "
                f"{e.bbox.w}x{e.bbox.h}  '{e.name}'  backend={e.backend}"
            )
    else:
        emit_json(payload)


# ----- native ---------------------------------------------------------------

@cli.group()
def native() -> None:
    """Find an application's own way to be driven (CLI / URL / shell)."""


@native.command("find")
@click.argument("app")
@click.option("--cli-name", "cli_names", multiple=True,
              help="a CLI name to look for (repeatable)")
@click.option("--scheme", "schemes", multiple=True,
              help="a URL scheme to look for (repeatable)")
@click.option("--exe", "exe_names", multiple=True,
              help="an App Paths entry to look for (repeatable)")
@json_option
def native_find(app: str, cli_names: tuple[str, ...], schemes: tuple[str, ...],
                exe_names: tuple[str, ...], as_json: bool) -> None:
    """Report the native surfaces an application offers.

    A native surface is cheaper and more deterministic than driving the
    window: a command line, a URL scheme, a registered executable or a
    Store AUMID. When none is found the report says so, so "nothing
    found" is never confused with "nothing looked for".
    """
    from openeyes.core.native import discover
    try:
        report = discover(app, cli_names=list(cli_names) or None,
                          schemes=list(schemes) or None,
                          exe_names=list(exe_names) or None)
    except Exception as exc:
        emit_error(f"could not look for native surfaces of {app!r}: {exc}",
                   code=4, app=app)
        return
    emit_json(report.to_dict())
    if not report.found:
        sys.exit(2)


# ----- webview -------------------------------------------------------------

@cli.group()
def webview() -> None:
    """Reach the DOM inside a WebView2 desktop app (Tauri / Wails / Electron)."""


@webview.command("describe")
@click.option("--port", type=int, default=9333, show_default=True)
@json_option
def webview_describe(port: int, as_json: bool) -> None:
    """Report whether a web view exposes a DevTools endpoint."""
    from openeyes.backends import webview2
    report = webview2.describe(port)
    emit_json(report)
    if not report["reachable"]:
        sys.exit(2)


@webview.command("launch")
@click.option("--exe", required=True, help="the desktop shell to start")
@click.option("--port", type=int, default=9333, show_default=True)
@click.option("--wait", type=float, default=20.0, show_default=True)
@json_option
def webview_launch(exe: str, port: int, wait: float, as_json: bool) -> None:
    """Start a shell with a DevTools port for its web view.

    The shell must be closed first: the environment variable that opens
    the port is only read when the web view is created.
    """
    from openeyes.backends import webview2
    report = webview2.launch_shell(exe, port=port, wait_s=wait)
    emit_json(report)
    if not report["launched"]:
        sys.exit(3)


# ----- app lifecycle -------------------------------------------------------

@cli.group()
def app() -> None:
    """Inspect and revive an app (window gone / process gone)."""


@app.command("state")
@click.option("--title-contains")
@click.option("--class-name")
@click.option("--process-name")
@json_option
def app_state_cmd(title_contains: str | None, class_name: str | None,
                 process_name: str | None, as_json: bool) -> None:
    """Report whether the app is running, window-gone, or not running."""
    from openeyes.core.lifecycle import app_state
    try:
        state = app_state(title_contains=title_contains, class_name=class_name,
                          process_name=process_name)
    except Exception as exc:
        emit_error(f"could not read the app state: {exc}", code=4)
        return
    emit_json(state.to_dict())


@app.command("ensure")
@click.option("--title-contains")
@click.option("--class-name")
@click.option("--process-name")
@click.option("--exe", default=None, help="executable to start")
@click.option("--aumid", default=None,
              help="Store app id, e.g. Family!App")
@click.option("--timeout", type=float, default=30.0, show_default=True)
@click.option("--dry-run/--go", default=True,
              help="default only reports what would be done")
@json_option
def app_ensure_cmd(title_contains: str | None, class_name: str | None,
                  process_name: str | None, exe: str | None, aumid: str | None,
                  timeout: float, dry_run: bool, as_json: bool) -> None:
    """Make sure the app is alive and usable, then prove it."""
    from openeyes.core.lifecycle import ensure_app
    try:
        result = ensure_app(title_contains=title_contains,
                            class_name=class_name,
                            process_name=process_name, exe=exe, aumid=aumid,
                            timeout=timeout, dry_run=dry_run)
    except Exception as exc:
        emit_error(f"could not ensure the app: {exc}", code=4)
        return
    emit_json(result.to_dict())
    if not result.ok and not dry_run:
        sys.exit(5)


def main() -> int:
    """Console entrypoint."""
    try:
        cli()
    except click.UsageError as e:
        click.echo(str(e), err=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
