"""Windows backend for the app-lifecycle primitives.

Three platform facts this module encodes:

* **A minimised window has an empty accessibility tree.** ``ShowWindow``
  with ``SW_SHOWNOACTIVATE`` repopulates it *without* taking the
  foreground, so the user keeps whatever they were typing.
* **Session timeout closes the window, not always the process.** Both
  cases are distinguished: a live process with no window needs a window
  restore, a dead process needs a relaunch.
* **Store apps launch through the shell, not the exe.** ``explorer.exe
  shell:AppsFolder\\<AUMID>`` is the reliable way to start an MSIX app
  whose executable lives inside a protected package folder.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WindowProbe:
    """What the lifecycle backend needs to know about a window.

    Kept private to this module: the public ``WindowInfo`` schema stays
    untouched, because the lifecycle tier needs one extra fact - whether
    the window is minimised (and therefore has an empty accessibility tree).
    """

    hwnd: int
    pid: int
    title: str
    class_name: str
    minimized: bool = False


SW_SHOWNOACTIVATE = 4  # restore without activating
SW_MINIMIZE = 6
SW_RESTORE = 9


def _win32():
    import win32gui
    import win32process
    return win32gui, win32process


def _owner_may_be(wanted: str, pid: int) -> bool:
    """Whether the window's owning process may belong to ``wanted``.

    A packaged application's window is owned by the framework host, so
    the owner rarely equals the application name. The check is therefore
    permissive: it only rejects an owner that is plainly a *different*
    application with no package relationship.
    """
    owner = _process_name_of(pid).lower()
    if owner.endswith(".exe"):
        owner = owner[:-4]
    if not owner:
        return True
    # "AnyVPN" and "Any VPN" are the same name written differently.
    if owner.replace(" ", "") == wanted.replace(" ", ""):
        return True
    # A framework host carries no identity of its own; the caller
    # resolves the real application separately.
    return owner in ("applicationframehost", "applicationframewindow",
                     "windows.ui.core.corewindow")


def _package_of_window(process_name: str) -> str:
    """The package directory a process belongs to, if any ("" otherwise).

    A packaged application runs under a framework host
    (``ApplicationFrameHost``), so the window's owning process says
    nothing about which application the window belongs to. The
    application is found through the package that owns the host.
    """
    try:
        import psutil
    except ImportError:
        return ""
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            exe = (proc.info.get("exe") or "").lower()
        except Exception:
            continue
        if name == process_name + ".exe" and exe and "windowsapps" in exe:
            parts = exe.split("\\")
            if len(parts) >= 2:
                return parts[-2]
    return ""


def _process_name_of(pid: int) -> str:
    """Best-effort process name for a pid ("" when unknown)."""
    if not pid:
        return ""
    try:
        import psutil
    except ImportError:
        return ""
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def find_window(*, title_contains: str | None = None,
                class_name: str | None = None,
                process_name: str | None = None) -> WindowProbe | None:
    """Find the first visible top-level window matching the filters.

    A minimised window still counts as visible here: it is found, and the
    caller can restore it without stealing focus.

    ``process_name`` filters on the process that **owns** the window, not
    on the process that happens to be running. Without that filter a
    lookup by title alone can return another application's window and
    report a state that contradicts the process evidence.
    """
    win32gui, win32process = _win32()
    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    wanted = (process_name or "").strip().lower()
    if wanted.endswith(".exe"):
        wanted = wanted[:-4]
    hits: list[WindowProbe] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        cls = win32gui.GetClassName(hwnd) or ""
        if title_contains and title_contains.lower() not in title.lower():
            return
        if class_name and class_name.lower() not in cls.lower():
            return
        try:
            pid = int(win32process.GetWindowThreadProcessId(hwnd)[1])
        except Exception:
            pid = 0
        # A packaged application's window belongs to the framework host
        # (ApplicationFrameHost), never to the application itself, so the
        # owning process is often NOT the application. The window identity
        # (its title and class) is the primary signal; the process is a
        # secondary one.
        if (wanted and not _owner_may_be(wanted, pid)
                and not _package_of_window(_process_name_of(pid).lower())):
            return
        hits.append(WindowProbe(
            hwnd=int(hwnd), pid=pid, title=title, class_name=cls,
            minimized=bool(win32gui.IsIconic(hwnd)),
        ))

    win32gui.EnumWindows(cb, None)
    return hits[0] if hits else None


def state_for_window(hwnd: int):
    """State of an explicitly given window handle."""
    from openeyes.core.lifecycle import STATE_NOT_RUNNING, STATE_RUNNING, AppState

    win32gui, win32process = _win32()
    if not hwnd or not win32gui.IsWindow(hwnd):
        return AppState(state=STATE_NOT_RUNNING,
                        hwnd=0, pid=0,
                        detail="window handle is no longer valid")
    try:
        pid = int(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        pid = 0
    return AppState(
        state=STATE_RUNNING,
        hwnd=int(hwnd),
        pid=pid,
        detail="window handle is valid",
        evidence={
            "title": win32gui.GetWindowText(hwnd) or "",
            "class_name": win32gui.GetClassName(hwnd) or "",
            "minimized": bool(win32gui.IsIconic(hwnd)),
        },
    )


def _package_matches(package_dir: str, wanted: str) -> bool:
    """Whether a package directory belongs to the wanted application.

    "AnywaySoftInc.AnyVPN_1.0.8.0_x64__0qkrc2qacwvfm" against "Any VPN":
    the punctuation and the case differ, and the version suffix has to
    go. What survives is the two halves of the package name, either of
    which may be the part the caller knows.
    """
    stem = package_dir.split("_")[0].lower()
    if not stem:
        return False
    parts = [p for p in re.split(r"[^a-z0-9]+", stem) if p]
    wanted_parts = [p for p in re.split(r"[^a-z0-9]+", wanted.lower()) if p]
    if not parts or not wanted_parts:
        return False
    # "anyvpn" against "anyvpn" (the display name collapsed) or against
    # "any" + "vpn" (the display name's own words).
    collapsed = "".join(parts)
    if collapsed == "".join(wanted_parts):
        return True
    if "".join(wanted_parts) in collapsed:
        return True
    # The wanted name may be one of the package's words, e.g. "vpn" in
    # "anyvpn".
    return all(part in collapsed for part in wanted_parts)


def process_alive(process_name: str) -> tuple[bool, str]:
    """Whether a process with this name is running.

    ``process_name`` may be given with or without the ``.exe`` suffix.
    Returns ``(alive, detail)``.

    A packaged (Store / MSIX) application rarely runs under its display
    name: Any VPN shows up as ``VpnGui.exe`` inside WindowsApps, and its
    window belongs to ``ApplicationFrameHost.exe``. Matching by name
    alone would therefore report such an application as not running
    while its window is plainly on screen, so a name that matches no
    process also looks for a **package** of that name - the package
    directory carries the real executable.
    """
    wanted = process_name.strip().lower()
    if wanted.endswith(".exe"):
        wanted = wanted[:-4]
    if not wanted:
        return False, "no process name given"

    try:
        import psutil
    except ImportError:
        psutil = None  # type: ignore[assignment]

    if psutil is not None:
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                exe = (proc.info.get("exe") or "").lower()
            except Exception:
                continue
            if name == wanted + ".exe" or name == wanted:
                return True, f"process {name} is running (pid {proc.pid})"
            # A packaged application: the executable lives in a directory
            # named after the package, e.g.
            # ...\WindowsApps\AnywaySoftInc.AnyVPN_1.0.8.0_x64__...\
            # The display name ("Any VPN") and the package name
            # ("AnywaySoftInc.AnyVPN") differ in punctuation and case, so
            # the comparison ignores them.
            if exe and "windowsapps" in exe:
                package = exe.rsplit("\\", 2)[-2].lower() if "\\" in exe else ""
                if package and _package_matches(package, wanted):
                    return True, (f"process {name} is running (pid {proc.pid}) "
                                  f"from package {package!r}")
        return False, f"no process named {wanted!r} is running"

    # Fallback: tasklist is slower but always present on Windows.
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {wanted}.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:
        return False, f"could not query processes: {exc}"
    if wanted in (out.stdout or "").lower():
        return True, f"process {wanted}.exe is running"
    return False, f"no process named {wanted!r} is running"


def restore_window(hwnd: int) -> dict:
    """Restore a minimised/hidden window without taking the foreground."""
    win32gui, _ = _win32()
    report: dict[str, Any] = {"ok": False, "restored": False,
                               "detail": "", "foreground_stolen": False}
    if not hwnd or not win32gui.IsWindow(hwnd):
        report["detail"] = "window handle is no longer valid"
        return report

    before = win32gui.GetForegroundWindow()
    try:
        iconic = bool(win32gui.IsIconic(hwnd))
        visible = bool(win32gui.IsWindowVisible(hwnd))
        if iconic or not visible:
            win32gui.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            time.sleep(0.35)
        after = win32gui.GetForegroundWindow()
        report["foreground_stolen"] = bool(before != after and after == hwnd)
        report["restored"] = True
        report["ok"] = True
        report["detail"] = ("restored without activating" if (iconic or not visible)
                             else "window was already visible")
    except Exception as exc:
        report["detail"] = f"restore failed: {exc}"
    return report


def launch_app(*, exe: str | None = None, aumid: str | None = None,
               args: tuple[str, ...] = (),
               working_dir: str | None = None) -> dict:
    """Start an app. ``launched`` means the start was issued, not that the
    app is already usable — use ``ensure_app`` for that proof."""
    report: dict[str, Any] = {"launched": False, "method": "", "detail": "",
                               "pid": 0}

    if aumid:
        # Store / MSIX app: the shell owns the real executable.
        target = aumid if aumid.lower().startswith("shell:") else (
            f"shell:AppsFolder\\{aumid}")
        try:
            subprocess.Popen(["explorer.exe", target],
                             cwd=working_dir or None)
            report["launched"] = True
            report["method"] = "aumid"
            report["detail"] = f"started {target}"
            return report
        except Exception as exc:
            report["detail"] = f"aumid launch failed: {exc}"
            return report

    if exe:
        try:
            proc = subprocess.Popen([exe, *args], cwd=working_dir or None)
            report["launched"] = True
            report["method"] = "exe"
            report["pid"] = int(proc.pid)
            report["detail"] = f"started {exe}"
            return report
        except Exception as exc:
            report["detail"] = f"exe launch failed: {exc}"
            return report

    report["detail"] = "need exe or aumid"
    return report


def env_snapshot() -> dict:
    """Small environment facts a supervisor may want to log."""
    return {
        "cwd": os.getcwd(),
        "user": os.environ.get("USERNAME", ""),
        "session": os.environ.get("SESSIONNAME", ""),
    }
