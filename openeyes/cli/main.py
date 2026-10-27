"""`eyes` CLI entrypoint.

Usage:
    eyes windows list [--title-contains <str>] [--class-name <str>] [--regex <re>]
    eyes capture --window <hwnd>|--screen --out <png> [--json]
    eyes detect --window <hwnd> [--restore] [--pretty] [--name-contains <str>]
                [--control-type <str>] [--regex <re>]
    eyes click  --window <hwnd> [--name-contains <str>] [--control-type <str>]
                [--regex <re>] [--x N --y N] [--dry-run|--go]
                [--button left|right|middle] [--double] [--return-to-origin]
    eyes grid   --window <hwnd> --row R --col C [--rows N --cols N] [--dry-run|--go]
    eyes hotkey [--combo ctrl+a]      # or: eyes send hotkey --combo ctrl+a
    eyes type   --text <str> [--interval N]
    eyes record replay <trajectory.jsonl> [--json]
    eyes version

Default click behavior is **dry-run**. Pass --go to actually click.
"""
from __future__ import annotations

import json
import sys
import time

import click

from openeyes import __version__
from openeyes.core.actuator import click_xy, send_hotkey, type_text
from openeyes.core.capture import capture_screen, capture_window
from openeyes.core.selector import detect_elements, find_elements
from openeyes.core.windows import find_window


@click.group()
@click.version_option(version=__version__, prog_name="eyes")
def cli() -> None:
    """OpenEyes — AI-friendly computer-use primitives."""


# ----- windows --------------------------------------------------------------

@cli.group()
def windows() -> None:
    """List / find top-level windows."""


@windows.command("list")
@click.option("--title-contains")
@click.option("--class-name")
@click.option("--regex")
def windows_list(title_contains: str | None, class_name: str | None, regex: str | None) -> None:
    """Enumerate visible top-level windows."""
    wins = find_window(title_contains=title_contains,
                       class_name=class_name, regex=regex)
    click.echo(json.dumps([w.to_dict() for w in wins], ensure_ascii=False, indent=2))


# ----- capture --------------------------------------------------------------

@cli.command()
@click.option("--window", type=int, default=0, help="hwnd to capture (0 = full screen)")
@click.option("--out", required=True, help="output PNG path")
@click.option("--json", "as_json", is_flag=True, help="write metadata JSON next to image")
def capture(window: int, out: str, as_json: bool) -> None:
    """Capture a window or the full screen to PNG."""
    img = capture_window(window) if window else capture_screen()
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
@click.option("--depth", type=int, default=12)
@click.option("--name-contains")
@click.option("--control-type")
@click.option("--regex")
@click.option("--max", type=int, default=200)
@click.option("--pretty", is_flag=True)
def detect(window: int, restore: bool, depth: int,
           name_contains: str | None, control_type: str | None, regex: str | None,
           max: int, pretty: bool) -> None:
    """Enumerate interactive elements in a window."""
    try:
        elems = detect_elements(window, restore=restore, max_depth=depth)
    except BaseException as e:
        msg = str(e) or repr(e)
        click.echo(f"detect failed for hwnd={window}: {msg}", err=True)
        sys.exit(4)
    elems = find_elements(elems, name_contains=name_contains,
                          control_type=control_type, regex=regex)
    elems = elems[:max]
    if pretty:
        for e in elems:
            click.echo(
                f"[{e.control_type:>14}] ({e.center.x:>4},{e.center.y:>4}) "
                f"{e.bbox.w}x{e.bbox.h}  '{e.name}'"
            )
    else:
        click.echo(json.dumps([e.to_dict() for e in elems], ensure_ascii=False, indent=2))


# ----- status ---------------------------------------------------------------

@cli.command()
@click.option("--window", type=int, required=True)
@click.option("--restore", is_flag=True)
@click.option("--max", type=int, default=30)
def status(window: int, restore: bool, max: int) -> None:
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
    elems = detect_elements(window, restore=restore)
    seen = set()
    print(f"[status] hwnd={window} total_elements={len(elems)}")
    shown = 0
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
        print(f"  Button @ ({e.center.x},{e.center.y}) {e.bbox.w}x{e.bbox.h}  {e.name!r}")
        shown += 1
        if shown >= max:
            break
    if shown == 0:
        print("  (no action-verb buttons found)")


# ----- click ----------------------------------------------------------------

@cli.command("click")
@click.option("--window", type=int, default=0)
@click.option("--x", type=int)
@click.option("--y", type=int)
@click.option("--name-contains")
@click.option("--control-type")
@click.option("--regex")
@click.option("--button", type=click.Choice(["left", "right", "middle"]), default="left")
@click.option("--double", is_flag=True)
@click.option("--return-to-origin", is_flag=True)
@click.option("--focus-titlebar", "focus_titlebar", is_flag=True,
              help="click title bar first to ensure window focus (UWP-safe)")
@click.option("--go", "go", is_flag=True,
              help="actually click. Default is dry-run.")
def tap(window: int, x: int | None, y: int | None,
          name_contains: str | None, control_type: str | None, regex: str | None,
          button: str, double: bool, return_to_origin: bool,
          focus_titlebar: bool, go: bool) -> None:
    """Click by coordinates OR by element selector.

    By default the click is dry-run — only the resolved target is printed.
    Pass --go to actually click.
    """
    if x is not None and y is not None:
        cx, cy = x, y
        desc = f"@ ({cx},{cy})"
    elif name_contains or control_type or regex:
        if not window:
            raise click.UsageError("--window is required for selector-based click")
        elems = detect_elements(window, restore=True)
        matches = find_elements(elems, name_contains=name_contains,
                                control_type=control_type, regex=regex)
        if not matches:
            click.echo("no matching element", err=True)
            sys.exit(3)
        target = matches[0]
        cx, cy = target.center.x, target.center.y
        desc = (f"@ ({cx},{cy})  name={target.name!r} "
                f"control_type={target.control_type!r}")
    else:
        raise click.UsageError("need --x/--y OR --name-contains/--control-type/--regex")

    if not go:
        click.echo(f"[dry-run] would click {desc}")
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
    click_xy(cx, cy, button=button, double=double)
    if return_to_origin:
        win32api.SetCursorPos((ox, oy))
    click.echo(f"clicked {desc}")


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
def hotkey(combo: str) -> None:
    """Press a hotkey chord, e.g. eyes hotkey --combo ctrl+a."""
    keys = [k.strip() for k in combo.split("+") if k.strip()]
    send_hotkey(*keys)
    click.echo(f"sent hotkey {combo}")


@cli.command()
@click.option("--text", required=True)
@click.option("--interval", type=float, default=0.0)
def type(text: str, interval: float) -> None:
    """Type a literal string."""
    type_text(text, interval=interval)
    click.echo(f"typed {len(text)} chars")


# ----- browser (CDP) -------------------------------------------------------

@cli.group()
def record() -> None:
    """Inspect recorded trajectories."""


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

@cli.group()
def browser() -> None:
    """Drive any Chromium browser (Edge / Chrome) via CDP.

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
def browser_tabs(port: int) -> None:
    """List DevTools page targets on the configured port."""
    from openeyes.backends import cdp as browser_backend
    try:
        pages = browser_backend.list_tabs(port)
    except Exception as e:
        click.echo(f"failed to reach :{port}: {e}", err=True)
        sys.exit(4)
    short = [{"id": p.get("id"), "title": p.get("title"),
              "url": p.get("url"), "type": p.get("type")} for p in pages]
    click.echo(json.dumps(short, ensure_ascii=False, indent=2))


@browser.command("scan")
@click.option("--port", type=int, default=9222, show_default=True)
@click.option("--url", default=None, help="navigate first (default: skip)")
@click.option("--url-contains", default=None)
@click.option("--name-contains")
@click.option("--control-type")
@click.option("--regex")
@click.option("--max", type=int, default=200)
@click.option("--pretty", is_flag=True)
@click.option("--no-hints", is_flag=True,
              help="skip letter-hint assignment (faster, no annotations)")
def browser_scan(port: int, url: str | None, url_contains: str | None,
                  name_contains: str | None, control_type: str | None,
                  regex: str | None, max: int, pretty: bool,
                  no_hints: bool) -> None:
    """DOM probe the active page; return interactive elements as JSON."""
    from openeyes.backends import cdp as browser_backend
    from openeyes.core.hints import assign_hints
    from openeyes.core.selector import find_elements
    conn = browser_backend.connect(port=port, url_contains=url_contains)
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


def _resolve_browser_target(conn, port: int, hint: str | None,
                              idx: int | None, name_contains: str | None,
                              control_type: str | None,
                              url_contains: str | None):
    """Pick one element out of a fresh DOM scan (click/type entry-point)."""
    from openeyes.backends import cdp as browser_backend
    from openeyes.core.hints import assign_hints, find_by_hint
    from openeyes.core.selector import find_elements
    if conn is None:
        conn = browser_backend.connect(port=port, url_contains=url_contains)
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
@click.option("--hint", default=None,
              help="Vimium-style letter (a, s, .. zz, aaa ..)")
@click.option("--idx", type=int, default=None,
              help="0-based index into the scan-ordered list")
@click.option("--name-contains", default=None)
@click.option("--control-type", default=None)
@click.option("--url-contains", default=None)
@click.option("--go", "go", is_flag=True, help="actually click")
def browser_click(port: int, hint: str | None, idx: int | None,
                   name_contains: str | None, control_type: str | None,
                   url_contains: str | None, go: bool) -> None:
    """Click a single element resolved by hint/idx/name-contains."""
    from openeyes.backends import cdp as browser_backend
    chosen, conn = _resolve_browser_target(
        None, port, hint, idx, name_contains, control_type, url_contains)
    desc = (f"[cdp] '{chosen.name}' ({chosen.control_type}) "
            f"center=({chosen.center.x},{chosen.center.y})")
    if not go:
        click.echo(f"[dry-run] would click {desc}")
        return
    browser_backend.click_center(conn, chosen)
    click.echo(f"clicked {desc}")


@browser.command("type")
@click.option("--port", type=int, default=9222, show_default=True)
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
def browser_type(port: int, text: str, hint: str | None, idx: int | None,
                  name_contains: str | None, control_type: str | None,
                  enter: bool, source_text: str | None,
                  url_contains: str | None) -> None:
    """Type text into a focused element (or specify target by hint/idx)."""
    from openeyes.backends import cdp as browser_backend
    from openeyes.core.safety import validate_text_entry
    chosen = None
    if hint or idx is not None or name_contains or control_type:
        chosen, conn = _resolve_browser_target(
            None, port, hint, idx, name_contains, control_type, url_contains)
    else:
        conn = browser_backend.connect(port=port, url_contains=url_contains)
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
@click.option("--out", required=True, help="output PNG path")
def browser_shot(port: int, out: str) -> None:
    """Capture the page viewport as PNG."""
    from openeyes.backends import cdp as browser_backend
    conn = browser_backend.connect(port=port)
    p = browser_backend.screenshot(conn, out)
    click.echo(p)


# ----- vision ---------------------------------------------------------------

@cli.command("vision")
@click.option("--detections", required=True,
              help="OmniParser-style detection JSON to convert into elements")
@click.option("--origin-x", type=int, default=0, show_default=True,
              help="image-to-window offset on X")
@click.option("--origin-y", type=int, default=0, show_default=True,
              help="image-to-window offset on Y")
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
def resolve(window: int, port: int, url_contains: str | None,
            vision_detections: str | None, max: int, pretty: bool) -> None:
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
        payload = result.to_dict()
        payload["elements"] = payload["elements"][:max]
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


# ----- app lifecycle -------------------------------------------------------

@cli.group()
def app() -> None:
    """Inspect and revive an app (window gone / process gone)."""


@app.command("state")
@click.option("--title-contains")
@click.option("--class-name")
@click.option("--process-name")
def app_state_cmd(title_contains: str | None, class_name: str | None,
                 process_name: str | None) -> None:
    """Report whether the app is running, window-gone, or not running."""
    from openeyes.core.lifecycle import app_state
    state = app_state(title_contains=title_contains, class_name=class_name,
                     process_name=process_name)
    click.echo(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))


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
def app_ensure_cmd(title_contains: str | None, class_name: str | None,
                  process_name: str | None, exe: str | None, aumid: str | None,
                  timeout: float, dry_run: bool) -> None:
    """Make sure the app is alive and usable, then prove it."""
    from openeyes.core.lifecycle import ensure_app
    result = ensure_app(title_contains=title_contains, class_name=class_name,
                       process_name=process_name, exe=exe, aumid=aumid,
                       timeout=timeout, dry_run=dry_run)
    click.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


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
