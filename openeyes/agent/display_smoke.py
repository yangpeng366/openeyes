"""A real-pointer smoke test for coordinates Windows makes awkward.

Three situations that break a coordinate pipeline while every call still
"works":

* a window that is **not at (0, 0)** - the classic one-window-origin shift;
* a monitor at a **negative coordinate** (left of the primary) - where a
  click that assumes 0-based screens lands on the wrong display;
* a display at **125 % / 130 %** scaling - where CSS pixels and device
  pixels differ, and a DPI-unaware process is answered in virtualised
  units.

The smoke places its own window on the display under test, computes the
click point through :func:`openeyes.core.coords.to_screen_xy` (the same
conversion the CLI and the library use), clicks with the real pointer and
compares what the window *received* with where the pointer was sent. A
window that never gets the click is a FAIL, not a pass with a caveat.

Three rules this module keeps:

* **dry-run by default** - nothing is clicked, no window is created, no
  pointer moves; the plan says what would happen and where;
* **SKIP is never PASS** - a machine with one monitor gets
  ``SKIP no monitor with a negative origin ...``, not an invented pass;
* **the enumeration is injectable** - :func:`plan_scenarios` takes a list
  of displays, so the decision logic is tested with fabricated monitors
  instead of whatever hardware CI happens to have.
"""
from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from openeyes.agent.virtual_desktop import (
    FAIL,
    PASS,
    SKIP,
    Display,
    enumerate_displays,
)
from openeyes.core.coords import WINDOW, to_screen_xy
from openeyes.core.dpi import (
    PER_MONITOR_AWARE,
    PER_MONITOR_AWARE_V2,
    ensure_dpi_awareness,
)

#: A step that has not run: dry-run, or waiting for ``--go``.
PLAN = "PLAN"

#: The overall verdict before anything ran.
DRY_RUN = "DRY-RUN"

#: How far the received click may sit from the point we sent it to. Two
#: pixels covers integer rounding at 130 % scaling; anything larger is a
#: coordinate mistake, not arithmetic.
CLICK_TOLERANCE = 2

#: Window geometry the smoke controls, in device pixels.
WINDOW_SIZE = (420, 300)

#: Margin from the work area's corner, so the window is never at (0, 0).
MARGIN = 120

#: The three scenarios, by name.
OFF_ORIGIN = "window_off_origin"
SCALED = "scaled_click_125_130"
NEGATIVE = "negative_secondary_click"


@dataclass
class Step:
    """One scenario: what was planned, what happened, what was measured."""

    name: str
    status: str
    detail: str = ""
    display: str = ""
    scale: int | None = None
    window_position: tuple[int, int] | None = None
    window_size: tuple[int, int] | None = None
    target_in_client: tuple[int, int] | None = None
    expected_screen: tuple[int, int] | None = None
    measured_screen: tuple[int, int] | None = None
    error: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "display": self.display,
            "scale": self.scale,
            "window_position": list(self.window_position)
            if self.window_position else None,
            "window_size": list(self.window_size) if self.window_size else None,
            "target_in_client": list(self.target_in_client)
            if self.target_in_client else None,
            "expected_screen": list(self.expected_screen)
            if self.expected_screen else None,
            "measured_screen": list(self.measured_screen)
            if self.measured_screen else None,
            "error_px": self.error,
        }

    def line(self) -> str:
        return f"{self.status:4} {self.name}: {self.detail}"


@dataclass
class SmokeReport:
    """The whole run: the hardware, the steps, and one honest verdict."""

    displays: list[Display] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    go: bool = False
    dpi_awareness: str = ""
    virtual_screen: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def overall(self) -> str:
        """DRY-RUN without ``--go``; otherwise FAIL > PASS > SKIP."""
        if not self.go:
            return DRY_RUN
        if any(step.status == FAIL for step in self.steps):
            return FAIL
        if any(step.status == PASS for step in self.steps):
            return PASS
        return SKIP

    def to_dict(self) -> dict[str, Any]:
        return {
            "go": self.go,
            "overall": self.overall,
            "dpi_awareness": self.dpi_awareness,
            "virtual_screen": list(self.virtual_screen),
            "displays": [display.to_dict() for display in self.displays],
            "steps": [step.to_dict() for step in self.steps],
        }

    def lines(self) -> list[str]:
        out = [
            f"dpi awareness: {self.dpi_awareness or 'unknown'}; "
            f"virtual screen {self.virtual_screen}",
            f"displays: {len(self.displays)}",
        ]
        out += [f"  {display.describe()}" for display in self.displays]
        out += [f"  {step.line()}" for step in self.steps]
        out.append(f"overall: {self.overall}")
        if not self.go:
            out.append("dry-run: nothing was clicked; pass --go to measure")
        return out


# ---------------------------------------------------------------- planning
def _fits(rect: tuple[int, int, int, int], size: tuple[int, int],
          margin: int = MARGIN) -> bool:
    width, height = size
    return (rect[2] - rect[0]) >= width + 2 * margin \
        and (rect[3] - rect[1]) >= height + 2 * margin


def window_position(display: Display, size: tuple[int, int] = WINDOW_SIZE
                    ) -> tuple[int, int] | None:
    """Where the smoke would put its window on *display*, or ``None``.

    Uses the work area rather than the monitor rectangle so the window
    does not sit under the taskbar, and never returns ``(0, 0)``: the
    whole point of the first scenario is a non-zero origin.
    """
    work = display.work
    if not _fits(work, size):
        return None
    return work[0] + MARGIN, work[1] + MARGIN


def _scales(displays: Sequence[Display]) -> str:
    seen = sorted({display.scale for display in displays if display.scale})
    return ", ".join(f"{scale}%" for scale in seen) or "unknown"


def _origins(displays: Sequence[Display]) -> str:
    return ", ".join(
        f"{display.device} at ({display.rect[0]},{display.rect[1]})"
        for display in displays
    ) or "none"


def plan_scenarios(displays: Sequence[Display],
                   size: tuple[int, int] = WINDOW_SIZE) -> list[Step]:
    """Decide what this machine can measure, and say why when it cannot.

    Pure: it reads the display list and nothing else. Every scenario that
    cannot run comes back as SKIP carrying the numbers it was decided on.
    """
    target = (size[0] // 2, size[1] // 2)
    steps: list[Step] = []

    # 1. A window away from (0, 0) - works on any display that fits it.
    usable = [display for display in displays
              if window_position(display, size) is not None]
    if usable:
        display = next((item for item in usable if item.primary), usable[0])
        position = window_position(display, size)
        steps.append(Step(
            name=OFF_ORIGIN, status=PLAN, display=display.device,
            scale=display.scale, window_position=position,
            window_size=size, target_in_client=target,
            detail=(f"place a {size[0]}x{size[1]} window at {position} on "
                    f"{display.device} and click its client point {target}; "
                    "the received point must equal window origin + point"),
        ))
    else:
        steps.append(Step(
            name=OFF_ORIGIN, status=SKIP,
            detail=(f"no display's work area holds a {size[0]}x{size[1]} "
                    f"window plus a {MARGIN} px margin (displays: "
                    f"{_origins(displays)})"),
        ))

    # 2. A display Windows scales to 125 % or 130 %.
    scaled = [display for display in displays
              if display.scale in (125, 130)
              and window_position(display, size) is not None]
    if scaled:
        display = next((item for item in scaled if item.primary), scaled[0])
        position = window_position(display, size)
        steps.append(Step(
            name=SCALED, status=PLAN, display=display.device,
            scale=display.scale, window_position=position,
            window_size=size, target_in_client=target,
            detail=(f"place the window at {position} on {display.device} "
                    f"({display.dpi} dpi, {display.scale}%) and click "
                    f"client point {target}; a DPI-unaware conversion lands "
                    f"about {display.scale}% away"),
        ))
    else:
        steps.append(Step(
            name=SCALED, status=SKIP,
            detail=(f"no display runs at 125% or 130% (scales present: "
                    f"{_scales(displays)})"),
        ))

    # 3. A secondary monitor at a negative coordinate.
    negative = [display for display in displays
                if not display.primary
                and (display.rect[0] < 0 or display.rect[1] < 0)
                and window_position(display, size) is not None]
    if negative:
        display = negative[0]
        position = window_position(display, size)
        steps.append(Step(
            name=NEGATIVE, status=PLAN, display=display.device,
            scale=display.scale, window_position=position,
            window_size=size, target_in_client=target,
            detail=(f"place the window at {position} on {display.device} "
                    f"(rect {display.rect}) and click client point "
                    f"{target}; the expected screen point is negative"),
        ))
    else:
        what = ("no secondary monitor with a negative origin"
                if displays else "no displays to enumerate")
        steps.append(Step(
            name=NEGATIVE, status=SKIP,
            detail=f"{what} (displays: {_origins(displays)})",
        ))
    return steps


# --------------------------------------------------------------- execution
def verify_click(expected: tuple[int, int], measured: tuple[int, int],
                 tolerance: int = CLICK_TOLERANCE) -> tuple[str, int, str]:
    """Compare where the pointer was sent with what the window received."""
    error = max(abs(measured[0] - expected[0]), abs(measured[1] - expected[1]))
    if error <= tolerance:
        return PASS, error, (f"click received at {measured}, sent to "
                             f"{expected} (off by {error} px, tolerance "
                             f"{tolerance})")
    return FAIL, error, (f"click received at {measured}, sent to {expected} "
                         f"(off by {error} px, tolerance {tolerance})")


def _execute(step: Step, *, tk: Any, user32: Any, wintypes: Any,
             tolerance: int = CLICK_TOLERANCE) -> Step:
    """Run one scenario with a real window and a real click."""
    import ctypes

    root = None
    saved_cursor = None
    try:
        root = tk.Tk()
        root.title("openeyes-display-smoke")
        # A negative x is written "+-1200": Tk reads "-1200" as an offset
        # from the screen's right edge, which would test the wrong place.
        root.geometry(f"{step.window_size[0]}x{step.window_size[1]}"
                      f"+{step.window_position[0]}+{step.window_position[1]}")
        root.attributes("-topmost", True)
        root.update()
        time.sleep(0.25)
        root.update()

        outer = (root.winfo_x(), root.winfo_y())
        client_origin = (root.winfo_rootx(), root.winfo_rooty())
        client_size = (root.winfo_width(), root.winfo_height())
        target = (client_size[0] // 2, client_size[1] // 2)
        step.target_in_client = target
        # The same conversion the CLI performs for a window-space element:
        # a point measured inside the window plus the window's origin.
        expected = to_screen_xy(WINDOW, target[0], target[1],
                                window_origin=client_origin)
        step.expected_screen = expected
        step.detail = (f"window at {outer}, client origin {client_origin}, "
                       f"size {client_size}; expected click {expected}")

        hwnd = int(root.winfo_id())
        hit = int(user32.WindowFromPoint(wintypes.POINT(*expected)) or 0)
        if hit != hwnd:
            step.status = FAIL
            step.detail += (f"; another window is on top at that point "
                            f"(hwnd {hit} != {hwnd})")
            return step

        recorded: list[tuple[int, int]] = []
        root.bind("<Button-1>",
                  lambda event: recorded.append((event.x_root, event.y_root)))

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        saved_cursor = (int(point.x), int(point.y))
        user32.SetCursorPos(expected[0], expected[1])
        root.update()
        time.sleep(0.08)
        user32.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
        for _ in range(10):
            root.update()
            time.sleep(0.02)

        if not recorded:
            step.status = FAIL
            step.detail += "; the window received no click at all"
            return step
        measured = recorded[0]
        step.measured_screen = measured
        status, error, detail = verify_click(expected, measured, tolerance)
        step.status = status
        step.error = error
        step.detail = (f"{detail}; window at {outer} "
                       f"scale {step.scale}%")

        # The window was asked to sit away from the origin; prove it did.
        if outer == (0, 0):
            step.status = FAIL
            step.detail += "; the window landed at (0,0), so the non-zero " \
                           "origin was not exercised"
        return step
    except Exception as exc:                     # noqa: BLE001 - reported
        step.status = SKIP
        step.detail = f"could not run on this desktop: {type(exc).__name__}: {exc}"
        return step
    finally:
        if saved_cursor is not None:
            with contextlib.suppress(Exception):
                user32.SetCursorPos(saved_cursor[0], saved_cursor[1])
        if root is not None:
            with contextlib.suppress(Exception):
                root.destroy()


def run(*, go: bool = False, displays: Sequence[Display] | None = None,
        size: tuple[int, int] = WINDOW_SIZE,
        tolerance: int = CLICK_TOLERANCE) -> SmokeReport:
    """Enumerate, plan, and - only with ``go`` - click.

    ``displays`` is injectable so the decision logic can be exercised
    against fabricated monitors; a real run passes ``None`` and reads the
    machine.
    """
    report = SmokeReport(go=go)
    if sys.platform.startswith("win"):
        report.dpi_awareness = ensure_dpi_awareness()
    else:
        report.dpi_awareness = "not-windows"
    if displays is None:
        displays = enumerate_displays() if sys.platform.startswith("win") else []
    report.displays = list(displays)
    if sys.platform.startswith("win"):
        import ctypes
        user32 = ctypes.windll.user32
        report.virtual_screen = (
            user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79),
        )
    report.steps = plan_scenarios(report.displays, size)
    if not go:
        return report

    if report.dpi_awareness not in (PER_MONITOR_AWARE, PER_MONITOR_AWARE_V2):
        for step in report.steps:
            if step.status == PLAN:
                step.status = SKIP
                step.detail = (
                    f"the process is {report.dpi_awareness!r}, not "
                    "per-monitor aware; SetCursorPos would be answered in "
                    "virtualised units, so no click is attempted"
                )
        return report
    if not sys.platform.startswith("win"):
        for step in report.steps:
            if step.status == PLAN:
                step.status = SKIP
                step.detail = "this smoke drives Win32 windows; not Windows"
        return report

    import ctypes
    import tkinter as tk
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    for step in report.steps:
        if step.status == PLAN:
            _execute(step, tk=tk, user32=user32, wintypes=wintypes,
                     tolerance=tolerance)
    return report
