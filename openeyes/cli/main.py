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
    eyes version

Default click behavior is **dry-run**. Pass --go to actually click.
"""
from __future__ import annotations
import json
import sys
import time
import click

from openeyes import __version__
from openeyes.core.windows import list_windows, find_window
from openeyes.core.capture import capture_window, capture_screen
from openeyes.core.selector import detect_elements, find_elements
from openeyes.core.actuator import click_xy, send_hotkey, type_text


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
    if window:
        img = capture_window(window)
    else:
        img = capture_screen()
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
    l, t, r, b = win32gui.GetWindowRect(window)
    cell_w = (r - l) / cols
    cell_h = (b - t) / rows
    cx = int(l + (col - 0.5) * cell_w)
    cy = int(t + (row - 0.5) * cell_h)
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