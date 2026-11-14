"""Explicit, verifiable window focus.

The actuator's :func:`openeyes.actuators.win32.focus_window` is best-effort
and returns a bool. This module wraps it with the report an agent needs:
the foreground hwnd before and after, whether the target really holds it,
and a structured reason when it does not.

Nothing here changes the foreground unless ``dry_run`` is false. A window
that is minimised, hidden, or has unusable geometry is reported as a
failure before the primitive is called - the primitive's title-bar fallback
must never be pointed at an off-screen rectangle.
"""
from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from typing import Any

ERROR_INVALID_HWND = "INVALID_HWND"
ERROR_WINDOW_MINIMIZED = "WINDOW_MINIMIZED"
ERROR_WINDOW_NOT_VISIBLE = "WINDOW_NOT_VISIBLE"
ERROR_WINDOW_GEOMETRY_INVALID = "WINDOW_GEOMETRY_INVALID"
ERROR_FOREGROUND_NOT_ACQUIRED = "FOREGROUND_NOT_ACQUIRED"
ERROR_FOCUS_STOLEN = "FOCUS_STOLEN"
ERROR_FOCUS_OCCLUDED = "FOCUS_OCCLUDED"
ERROR_BACKEND_ERROR = "BACKEND_ERROR"
ERROR_WINDOW_IDENTITY_CHANGED = "WINDOW_IDENTITY_CHANGED"
ERROR_UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"

PRIMITIVE = "openeyes.actuators.win32.focus_window"

# A foreground switch is asynchronous: the primitive can return before the
# window manager has moved the target to the front. Poll a few times (reads
# only, never any input) and keep the whole budget well under a second.
_SETTLE_INTERVAL_S = 0.05
_SETTLE_MAX_ATTEMPTS = 10
# A hard wall-clock cap for the whole settle loop. Counting 10 reads is not
# enough: a read that hangs or runs slowly would otherwise stretch the loop
# well past the intended sub-second budget.
_SETTLE_TOTAL_BUDGET_S = 1.0

Probe = Callable[[int], dict[str, Any]]
Foreground = Callable[[], int]
Focuser = Callable[[int], bool]
Restorer = Callable[[int], Any]
DpiProbe = Callable[[], str]
Sleeper = Callable[[float], None]
Clock = Callable[[], float]


def _foreground_hwnd() -> int:
    """Read the foreground hwnd. Never takes focus."""
    if not sys.platform.startswith("win"):
        return 0
    import win32gui

    return int(win32gui.GetForegroundWindow())


def _foreground_lock_timeout_ms() -> int | None:
    """The system foreground-lock timeout, or None when unreadable.

    A non-zero value is a likely reason ``SetForegroundWindow`` refuses a
    switch; the number is reported so a caller does not have to guess.
    """
    if not sys.platform.startswith("win"):
        return None
    import ctypes

    value = ctypes.c_uint(0)
    # SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
    ok = ctypes.windll.user32.SystemParametersInfoW(
        0x2000, 0, ctypes.byref(value), 0
    )
    return int(value.value) if ok else None


def _probe_window(hwnd: int) -> dict[str, Any]:
    """Read the target's identity and whether it is safe to activate."""
    if not sys.platform.startswith("win"):
        return {"valid": False, "platform": sys.platform}
    import win32gui
    import win32process

    if not hwnd or not win32gui.IsWindow(hwnd):
        return {"valid": False, "detail": "window handle is no longer valid"}

    minimized = bool(win32gui.IsIconic(hwnd))
    visible = bool(win32gui.IsWindowVisible(hwnd))
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        rect = {"left": int(left), "top": int(top),
                "right": int(right), "bottom": int(bottom)}
        geometry_ok = bool(
            right > left and bottom > top
            and left > -30000 and top > -30000
        )
    except Exception as exc:
        rect = None
        geometry_ok = False
        detail = f"could not read window rectangle: {exc}"
    else:
        detail = ""

    try:
        pid = int(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        pid = 0
    return {
        "valid": True,
        "minimized": minimized,
        "visible": visible,
        "geometry_ok": geometry_ok,
        "rect": rect,
        "detail": detail,
        "title": win32gui.GetWindowText(hwnd) or "",
        "class_name": win32gui.GetClassName(hwnd) or "",
        "pid": pid,
    }


def _default_dpi() -> str:
    from openeyes.core.dpi import ensure_dpi_awareness

    return ensure_dpi_awareness()


def _focus_with_existing_primitive(hwnd: int) -> bool:
    # Call the implementing function directly: it raises a structured
    # FocusRefused for an occluded title-bar point, which focus_window_checked
    # reports under FOCUS_OCCLUDED. The public win32.focus_window() keeps its
    # plain bool contract (False on a refusal) for every other caller.
    from openeyes.actuators.win32 import _focus_window_impl

    return bool(_focus_window_impl(hwnd))


def _settle_foreground(foreground: Foreground, hwnd: int, *,
                       sleep: Sleeper, attempts: int,
                       interval: float, budget_s: float,
                       clock: Clock) -> int | None:
    """Poll the foreground until it equals ``hwnd`` or the budget is spent.

    Only ``foreground`` (a read) and ``sleep`` are used - no clicks, no input,
    so the retry cannot itself disturb focus. A failed read is retried within
    the same budget rather than being turned into a number. Returns the last
    successfully-read hwnd, or ``None`` when every read failed (an unknown
    foreground that must never be reported as a value).

    ``budget_s`` is a *wall-clock* deadline (``clock`` = time.monotonic in
    production), not just an attempt count: a slow or hung read must not let
    the whole settle overrun. On timeout the last known reading is returned
    and the caller turns it into the verdict.
    """
    deadline = clock() + budget_s
    last: int | None = None
    for attempt in range(attempts):
        if clock() >= deadline:
            break
        try:
            raw = foreground()
            value = int(raw) if raw is not None else None
        except Exception:
            value = None
        if value == hwnd:
            return hwnd
        if value is not None:
            last = value
        if attempt < attempts - 1 and clock() < deadline:
            sleep(interval)
    return last


def _fail(report: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    report["ok"] = False
    report["error_code"] = code
    report["error"] = message
    report["reason"] = "error"
    timeout = report.get("foreground_lock_timeout_ms")
    if code == ERROR_FOREGROUND_NOT_ACQUIRED and timeout:
        note = (f"foreground lock timeout is {timeout} ms; Windows may be "
                "denying the switch")
        if note not in report["warnings"]:
            report["warnings"].append(note)
    return report


def _operability_failure(info: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(code, message)`` when a probe says the window is unsafe to
    activate, else ``None``.

    Shared by the first probe and the --go re-check so both reject on exactly
    the same criteria. A minimised, hidden or off-screen window must never
    reach the primitive's title-bar-click fallback - neither on the first
    read nor after a fresh read taken just before activation.
    """
    if info.get("minimized"):
        return (ERROR_WINDOW_MINIMIZED,
                "window is minimised; restore it before asking eyes focus")
    if not info.get("visible"):
        return (ERROR_WINDOW_NOT_VISIBLE,
                "window is not visible; eyes will not activate a hidden window")
    if info.get("geometry_ok") is False:
        detail = info.get("detail") or "window rectangle is off-screen or empty"
        return (ERROR_WINDOW_GEOMETRY_INVALID,
                f"window geometry is unsafe to activate: {detail}")
    return None


def _restore_window(hwnd: int) -> bool:
    """Un-minimise a window; the only visible side effect this module has.

    Callers opt in. Every read-only path in this module stays side-effect
    free, so a probe or a dry run never moves a window.
    """
    if not sys.platform.startswith("win"):
        return False
    import win32con
    import win32gui

    win32gui.ShowWindow(int(hwnd), win32con.SW_RESTORE)
    return True


def focus_window_checked(
    hwnd: int,
    *,
    dry_run: bool = True,
    focuser: Focuser | None = None,
    foreground: Foreground | None = None,
    probe: Probe | None = None,
    dpi: DpiProbe | None = None,
    sleep: Sleeper | None = None,
    clock: Clock | None = None,
    restore: bool = False,
    restorer: Restorer | None = None,
    settle_budget_s: float | None = None,
) -> dict[str, Any]:
    """Focus ``hwnd`` and report what actually happened.

    ``restore`` un-minimises the target first. It is opt-in because
    restoring is a visible side effect; without it a minimised window is
    still refused with ``WINDOW_MINIMIZED`` instead of being touched.

    ``dry_run`` inspects the target and reads the foreground but never calls
    the focus primitive. With ``dry_run=False`` the result is success only
    when the foreground is observed to be the target afterwards; the
    primitive's own bool is recorded but is not the verdict. Before the
    primitive runs, the window's identity and operability are re-checked so a
    recycled or now-unsafe hwnd is never activated. After it runs, the
    foreground is polled briefly (settle) under a wall-clock deadline because
    the switch is asynchronous.
    """
    hwnd = int(hwnd or 0)
    foreground = foreground or _foreground_hwnd
    probe = probe or _probe_window
    dpi = dpi or _default_dpi
    focuser = focuser or _focus_with_existing_primitive
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    restorer = restorer or _restore_window
    settle_budget_s = (_SETTLE_TOTAL_BUDGET_S if settle_budget_s is None
                       else settle_budget_s)

    report: dict[str, Any] = {
        "ok": False,
        "dry_run": bool(dry_run),
        "hwnd": hwnd,
        "foreground_before": 0,
        "foreground_after": 0,
        "focused": False,
        "caller_pid": caller_pid(),
        "same_process": False,
        "primitive": PRIMITIVE,
        "primitive_called": False,
        "primitive_result": None,
        "restored": False,
        "error": None,
        "error_code": None,
        "reason": None,
        "dpi_awareness": None,
        "foreground_lock_timeout_ms": None,
        "window": None,
        "warnings": [],
    }

    try:
        report["dpi_awareness"] = dpi()
    except Exception as exc:
        report["warnings"].append(f"dpi probe failed: {exc}")
    if report["dpi_awareness"] == "unaware":
        report["warnings"].append("dpi unaware")

    report["foreground_lock_timeout_ms"] = _foreground_lock_timeout_ms()

    try:
        before = int(foreground() or 0)
    except Exception as exc:
        return _fail(report, ERROR_BACKEND_ERROR,
                     f"could not read the foreground window: {exc}")
    report["foreground_before"] = before
    report["foreground_after"] = before
    report["focused"] = before == hwnd

    if hwnd <= 0:
        return _fail(report, ERROR_INVALID_HWND,
                     "a positive top-level window handle is required")

    try:
        info = probe(hwnd) or {}
    except Exception as exc:
        return _fail(report, ERROR_BACKEND_ERROR,
                     f"could not inspect hwnd={hwnd}: {exc}")
    report["window"] = info
    report["same_process"] = bool(
        info.get("pid") and info.get("pid") == report["caller_pid"]
    )

    if not info.get("valid"):
        detail = info.get("detail") or "window handle is no longer valid"
        if not sys.platform.startswith("win") and info.get("platform"):
            return _fail(report, ERROR_UNSUPPORTED_PLATFORM,
                         f"window focus is not implemented on {info['platform']}")
        return _fail(report, ERROR_INVALID_HWND, detail)

    if restore and info.get("minimized"):
        identity_before_restore = (
            info.get("pid"), info.get("title"), info.get("class_name"),
        )
        try:
            restorer(hwnd)
        except Exception as exc:
            report["warnings"].append(f"restore failed: {exc}")
        else:
            sleep(0.2)
            try:
                info = probe(hwnd) or {}
            except Exception as exc:
                return _fail(report, ERROR_BACKEND_ERROR,
                             f"could not re-inspect hwnd={hwnd} after restore: {exc}")
            report["window"] = info
            report["restored"] = True
            if (
                info.get("pid"), info.get("title"), info.get("class_name"),
            ) != identity_before_restore:
                return _fail(
                    report, ERROR_WINDOW_IDENTITY_CHANGED,
                    f"window identity changed while restoring hwnd={hwnd}",
                )
    rejection = _operability_failure(info)
    if rejection:
        return _fail(report, *rejection)

    if before == hwnd:
        report["ok"] = True
        report["focused"] = True
        report["reason"] = "already-foreground"
        return report

    if dry_run:
        report["ok"] = True
        report["reason"] = "dry-run"
        return report

    # --go: re-check the window's identity immediately before activating it.
    # Windows recycles HWNDs; a handle probed a moment ago can now point at a
    # different window. Comparing pid/title/class plus IsWindow (the probe's
    # ``valid`` flag) keeps a stale hwnd from ever reaching the primitive's
    # title-bar-click fallback.
    identity_before = (
        info.get("pid"), info.get("title"), info.get("class_name"),
    )
    try:
        fresh = probe(hwnd) or {}
    except Exception as exc:
        return _fail(report, ERROR_BACKEND_ERROR,
                     f"could not re-check window identity of hwnd={hwnd}: {exc}")
    identity_now = (
        fresh.get("pid"), fresh.get("title"), fresh.get("class_name"),
    )
    if not fresh.get("valid") or identity_now != identity_before:
        return _fail(
            report, ERROR_WINDOW_IDENTITY_CHANGED,
            f"window identity changed for hwnd={hwnd}; refusing to activate "
            "a stale handle",
        )

    # --go: re-verify the window is still *safe to click*, not just the same
    # window. Between the first probe and activation it can be minimised,
    # hidden, or moved off-screen. Reuse the first round's rejection criteria
    # so none of those states ever reaches the primitive's fallback click.
    rejection = _operability_failure(fresh)
    if rejection:
        return _fail(report, *rejection)

    # From here the primitive is about to run: the post-call foreground is
    # unknown until we actually read it. Never leave it holding ``before`` -
    # on a failed verification read that would fabricate a value we never saw.
    report["foreground_after"] = None
    report["focused"] = False
    report["primitive_called"] = True
    try:
        result = bool(focuser(hwnd))
    except Exception as exc:
        # The primitive's return value is *unknown* when it raises. Never
        # write that as False (which would mean "ran and returned failure") -
        # leave primitive_result=None so JSON can tell null from false. A
        # structured refusal from the primitive (an occluded title-bar point)
        # is reported under its own new code; the raised detail is preserved
        # in the message either way.
        report["primitive_result"] = None
        if getattr(exc, "focus_error_code", None) == ERROR_FOCUS_OCCLUDED:
            return _fail(
                report, ERROR_FOCUS_OCCLUDED,
                f"focus primitive refused an occluded click point: {exc}",
            )
        return _fail(report, ERROR_BACKEND_ERROR,
                     f"focus primitive raised: {exc}")
    report["primitive_result"] = result

    # A foreground switch is asynchronous. Poll (reads only, no input) before
    # deciding it failed; FOCUS_STOLEN is judged from the settle-after reading.
    after = _settle_foreground(
        foreground, hwnd, sleep=sleep,
        attempts=_SETTLE_MAX_ATTEMPTS, interval=_SETTLE_INTERVAL_S,
        budget_s=settle_budget_s, clock=clock,
    )
    if after is None:
        return _fail(
            report, ERROR_BACKEND_ERROR,
            "could not verify the foreground window: every read failed",
        )
    report["foreground_after"] = after
    report["focused"] = after == hwnd

    if after == hwnd:
        report["ok"] = True
        report["reason"] = "focused"
        if not result:
            report["warnings"].append(
                "focus primitive reported failure but the foreground was verified"
            )
        return report
    if after != before:
        return _fail(
            report, ERROR_FOCUS_STOLEN,
            f"focus was stolen: foreground is hwnd={after}, not hwnd={hwnd}",
        )
    return _fail(
        report, ERROR_FOREGROUND_NOT_ACQUIRED,
        "SetForegroundWindow did not take effect after settling: "
        f"foreground stayed hwnd={before}",
    )


def caller_pid() -> int:
    """The process that made the request; reported beside the target pid."""
    return os.getpid()
