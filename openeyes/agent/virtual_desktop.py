"""A second desktop: somewhere else to draw, on the same machine.

Windows organises a session into *desktops*. One of them is the
interactive desktop a person types into; the logon screen, the
screen-saver and anything a program creates with ``CreateDesktop`` are
others. A window belongs to exactly one desktop, so a window on a
second desktop is not something the person in front of the machine can
see or click - which is what makes it interesting: it is a place an
agent could draw a busy cursor without being in anybody's way.

The interesting part is not "can the window be created". It is whether
the window is a *real* window there - created, cut to the cursor
silhouette, painted with the right pixels - while the interactive
desktop is left exactly as it was: same foreground window, same pointer
position, no input sent. And then whether anything can look at the
result: ``PrintWindow`` reads a window's own painting, while a screen
grab needs a screen, and a desktop nobody is showing has none.

Everything here is measurement, not belief. Each check carries the
number it was decided on, and a thing this machine refuses to do is
reported as ``SKIP`` with the error code, never as a pass.

Nothing in this module moves the pointer, sends input, switches the
desktop (``SwitchDesktop`` is never called, so the person keeps looking
at their own desktop), or touches the display configuration. The
second desktop is created, measured, and closed.
"""
from __future__ import annotations

import contextlib
import ctypes
import itertools
import os
import sys
import threading
import time
from collections import Counter
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

PHYSICAL = "physical"
VIRTUAL = "virtual"

#: ``GetWindowRgn`` answers with the complexity of the region; a window
#: cut to a cursor silhouette is a COMPLEXREGION, not a rectangle.
COMPLEXREGION = 3

#: The access ``CreateDesktop`` needs to hand the desktop to this process.
_DESKTOP_ALL_ACCESS = 0x10000000  # GENERIC_ALL

#: ``EnumDisplayDevices``: this adapter is part of the visible desktop.
_ATTACHED_TO_DESKTOP = 0x1

#: The words that name a display driver that is not a piece of glass.
#: A software display (an IDD, an indirect display, a remote-desktop or
#: screen-sharing adapter) is installed as a driver, so the only honest
#: way to tell it apart is to read what the driver calls itself.
_VIRTUAL_HINTS = (
    "virtual", "idd", "indirect", "spacedesk", "parsec", "oray",
    "sunlogin", "todesk", "displaylink", "usb display", "mirage",
    "swdevice", "basic display",
)

#: Names, so several probes in one process never collide.
_COUNTER = itertools.count(1)

#: How long the main thread waits for the worker to put its window up.
_READY_TIMEOUT = 20.0


# ------------------------------------------------------------------ displays
class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _Rect),
                ("rcWork", _Rect), ("dwFlags", wintypes.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("DeviceName", ctypes.c_wchar * 32),
                ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", wintypes.DWORD),
                ("DeviceID", ctypes.c_wchar * 128),
                ("DeviceKey", ctypes.c_wchar * 128)]


@dataclass
class Display:
    """One monitor that is part of the desktop right now."""

    device: str
    rect: tuple[int, int, int, int]
    work: tuple[int, int, int, int]
    dpi: int | None
    scale: int | None
    primary: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "rect": list(self.rect),
            "work": list(self.work),
            "size": [self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]],
            "dpi": self.dpi,
            "scale": self.scale,
            "primary": self.primary,
        }

    def describe(self) -> str:
        width = self.rect[2] - self.rect[0]
        height = self.rect[3] - self.rect[1]
        dpi = f"{self.dpi} dpi" if self.dpi else "dpi unknown"
        scale = f" ({self.scale}%)" if self.scale else ""
        origin = f"at ({self.rect[0]},{self.rect[1]})"
        return (f"{self.device} {width}x{height} {origin} {dpi}{scale}"
                + (", primary" if self.primary else ""))


@dataclass
class Adapter:
    """One display adapter, whether or not it is driving a monitor."""

    device: str
    description: str
    device_id: str
    flags: int
    monitors: list[str] = field(default_factory=list)

    @property
    def attached(self) -> bool:
        return bool(self.flags & _ATTACHED_TO_DESKTOP)

    @property
    def kind(self) -> str:
        return classify_adapter(self.description, self.device_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "description": self.description,
            "device_id": self.device_id,
            "flags": self.flags,
            "attached": self.attached,
            "kind": self.kind,
            "monitors": list(self.monitors),
        }

    def describe(self) -> str:
        state = "attached" if self.attached else "not attached"
        return f"{self.device} {self.description} [{self.kind}, {state}]"


def classify_adapter(description: str, device_id: str = "") -> str:
    """Is this adapter a piece of glass, or a driver that pretends to be one?

    A software display registers itself as a display adapter, so it
    answers ``EnumDisplayDevices`` like any other. What gives it away is
    its name and where it sits in the device tree: a virtual display is
    a root-enumerated software device, a real one hangs off a bus.
    """
    text = f"{description} {device_id}".lower()
    if any(hint in text for hint in _VIRTUAL_HINTS):
        return VIRTUAL
    if device_id.lower().startswith("root\\"):
        return VIRTUAL
    return PHYSICAL


def _dpi_of(hmonitor: int) -> tuple[int | None, int | None]:
    """The effective DPI and the scale percentage Windows uses for a monitor."""
    shcore = ctypes.windll.shcore
    dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
    dpi: int | None = None
    if shcore.GetDpiForMonitor(ctypes.c_void_p(hmonitor), 0,
                               ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
        dpi = int(dpi_x.value)
    scale: int | None = None
    value = ctypes.c_int()
    if shcore.GetScaleFactorForMonitor(ctypes.c_void_p(hmonitor),
                                       ctypes.byref(value)) == 0:
        scale = int(value.value)
    return dpi, scale


def enumerate_displays() -> list[Display]:
    """Every monitor that is part of the desktop, as Windows reports it.

    ``ensure_dpi_awareness`` has to come first: a DPI-unaware caller is
    answered in the virtualised space and reads 96 dpi for a scaled
    display, which is the one answer this must not get wrong.
    """
    if not sys.platform.startswith("win"):
        return []
    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    user32 = ctypes.windll.user32
    callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.POINTER(_Rect),
                                  ctypes.c_double)
    found: list[Display] = []

    def collect(hmonitor, _hdc, _rect, _data):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return 1
        dpi, scale = _dpi_of(hmonitor)
        found.append(Display(
            device=info.szDevice,
            rect=(info.rcMonitor.left, info.rcMonitor.top,
                  info.rcMonitor.right, info.rcMonitor.bottom),
            work=(info.rcWork.left, info.rcWork.top,
                  info.rcWork.right, info.rcWork.bottom),
            dpi=dpi,
            scale=scale,
            primary=bool(info.dwFlags & 1),
        ))
        return 1

    user32.EnumDisplayMonitors(None, None, callback(collect), 0)
    return found


def enumerate_adapters() -> list[Adapter]:
    """Every display adapter Windows knows, attached or not.

    The ones that are not attached are the point: a virtual-display
    driver that is installed but idle shows up here and nowhere else,
    and calling that "a virtual monitor" would be a fiction.
    """
    if not sys.platform.startswith("win"):
        return []
    user32 = ctypes.windll.user32
    adapters: list[Adapter] = []
    index = 0
    while True:
        device = _DisplayDevice()
        device.cbSize = ctypes.sizeof(device)
        if not user32.EnumDisplayDevicesW(None, index,
                                          ctypes.byref(device), 0):
            break
        monitors: list[str] = []
        monitor_index = 0
        while True:
            monitor = _DisplayDevice()
            monitor.cbSize = ctypes.sizeof(monitor)
            if not user32.EnumDisplayDevicesW(device.DeviceName, monitor_index,
                                              ctypes.byref(monitor), 0):
                break
            monitors.append(monitor.DeviceString.strip() or monitor.DeviceName)
            monitor_index += 1
        adapters.append(Adapter(
            device=device.DeviceName,
            description=device.DeviceString.strip(),
            device_id=device.DeviceID.strip(),
            flags=int(device.StateFlags),
            monitors=monitors,
        ))
        index += 1
    return adapters


def virtual_screen() -> tuple[int, int, int, int]:
    """The rectangle the desktop spans, across every attached display."""
    if not sys.platform.startswith("win"):
        return (0, 0, 0, 0)
    user32 = ctypes.windll.user32
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


@dataclass
class Topology:
    """What the machine is showing, and what it could show."""

    displays: list[Display] = field(default_factory=list)
    adapters: list[Adapter] = field(default_factory=list)
    virtual_screen: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def attached_adapters(self) -> list[Adapter]:
        return [adapter for adapter in self.adapters if adapter.attached]

    @property
    def virtual_adapters(self) -> list[Adapter]:
        return [adapter for adapter in self.adapters
                if adapter.kind == VIRTUAL]

    @property
    def active_virtual_displays(self) -> list[Adapter]:
        """Virtual adapters that are actually driving part of the desktop."""
        return [adapter for adapter in self.virtual_adapters if adapter.attached]

    @property
    def second_display(self) -> Display | None:
        for display in self.displays:
            if not display.primary:
                return display
        return self.displays[1] if len(self.displays) > 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "displays": [display.to_dict() for display in self.displays],
            "adapters": [adapter.to_dict() for adapter in self.adapters],
            "virtual_screen": list(self.virtual_screen),
            "attached_adapters": len(self.attached_adapters),
            "virtual_adapters": [adapter.device
                                 for adapter in self.virtual_adapters],
            "active_virtual_displays": [adapter.device
                                        for adapter in self.active_virtual_displays],
        }

    def summary(self) -> list[str]:
        """The topology as lines a person can check against Settings."""
        first = self.displays[0] if self.displays else None
        primary = (f"{first.rect[2] - first.rect[0]}x"
                   f"{first.rect[3] - first.rect[1]}"
                   if first else "unknown")
        lines = [
            f"displays: {len(self.displays)} attached, primary {primary}, "
            f"virtual screen {self.virtual_screen}",
            f"adapters: {len(self.adapters)} present, "
            f"{len(self.attached_adapters)} attached, "
            f"{len(self.virtual_adapters)} virtual "
            f"({len(self.active_virtual_displays)} driving a monitor)",
        ]
        lines += [f"  {display.describe()}" for display in self.displays]
        lines += [f"  {adapter.describe()}" for adapter in self.adapters]
        return lines


def display_topology() -> Topology:
    """Everything the display APIs say about this machine, right now."""
    return Topology(displays=enumerate_displays(),
                    adapters=enumerate_adapters(),
                    virtual_screen=virtual_screen())


# ------------------------------------------------------------------- capture
#: ``BitBlt`` with a straight copy; a top-down 32-bit DIB to read it back.
_SRCCOPY = 0x00CC0020
_BI_RGB = 0
_DIB_RGB_COLORS = 0


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader),
                ("bmiColors", wintypes.DWORD * 3)]


_API: dict[str, Any] = {}


def _apis():
    """``user32`` and ``gdi32`` with the prototypes this probe needs.

    Everything here is straight GDI through ``ctypes``. Going through a
    wrapper that keeps its own handle table - pywin32's does - leaves the
    desktop busy after the window is gone, and a busy desktop refuses to
    be closed (``ERROR_BUSY``, 170), which would leave one behind per
    probe. Straight GDI releases what it takes.
    """
    if not _API:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.PrintWindow.restype = wintypes.BOOL
        user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       wintypes.UINT]
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
        gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p,
                                                 ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.BitBlt.restype = wintypes.BOOL
        gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                 ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.UINT, wintypes.UINT,
                                    ctypes.c_void_p, ctypes.POINTER(_BitmapInfo),
                                    wintypes.UINT]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        _API["user32"] = user32
        _API["gdi32"] = gdi32
    return _API["user32"], _API["gdi32"]


def _dib_image(memory: int, bitmap: int,
               size: tuple[int, int]) -> Image.Image:
    """Read a bitmap back as an RGB image."""
    _, gdi32 = _apis()
    width, height = size
    info = _BitmapInfo()
    info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height              # top-down
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = _BI_RGB
    buffer = ctypes.create_string_buffer(width * height * 4)
    rows = gdi32.GetDIBits(ctypes.c_void_p(memory), ctypes.c_void_p(bitmap), 0,
                           height, buffer, ctypes.byref(info),
                           _DIB_RGB_COLORS)
    if rows != height:
        raise OSError(f"GetDIBits returned {rows} rows of {height}")
    return Image.frombytes("RGB", (width, height), buffer.raw, "raw",
                           "BGRX", 0, 1)


def _copy_dc(source_hdc: int, size: tuple[int, int],
             origin: tuple[int, int] = (0, 0)) -> Image.Image:
    """Copy a rectangle out of a device context as an RGB image."""
    _, gdi32 = _apis()
    width, height = size
    memory = gdi32.CreateCompatibleDC(ctypes.c_void_p(source_hdc))
    bitmap = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(source_hdc),
                                          width, height)
    try:
        gdi32.SelectObject(ctypes.c_void_p(memory), ctypes.c_void_p(bitmap))
        if not gdi32.BitBlt(ctypes.c_void_p(memory), 0, 0, width, height,
                            ctypes.c_void_p(source_hdc), origin[0], origin[1],
                            _SRCCOPY):
            raise OSError(f"BitBlt failed, GetLastError={ctypes.get_last_error()}")
        return _dib_image(memory, bitmap, size)
    finally:
        if bitmap:
            gdi32.DeleteObject(ctypes.c_void_p(bitmap))
        if memory:
            gdi32.DeleteDC(ctypes.c_void_p(memory))


def _window_dc_pixels(hwnd: int, size: tuple[int, int]) -> Image.Image:
    """What the window itself paints, read back through its own DC."""
    user32, _ = _apis()
    hdc = user32.GetDC(ctypes.c_void_p(hwnd))
    try:
        return _copy_dc(hdc, size)
    finally:
        user32.ReleaseDC(ctypes.c_void_p(hwnd), ctypes.c_void_p(hdc))


def _desktop_screen_pixels(size: tuple[int, int],
                           origin: tuple[int, int]) -> Image.Image:
    """The screen surface of *this thread's* desktop, if it has one."""
    user32, _ = _apis()
    screen = user32.GetDC(None)
    try:
        return _copy_dc(screen, size, origin)
    finally:
        user32.ReleaseDC(None, ctypes.c_void_p(screen))


def _print_window(hwnd: int, size: tuple[int, int],
                  flags: int = 2) -> tuple[bool, Image.Image]:
    """Ask the window to paint itself into a bitmap (``PW_RENDERFULLCONTENT``)."""
    user32, gdi32 = _apis()
    width, height = size
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(ctypes.c_void_p(screen))
    bitmap = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(screen),
                                          width, height)
    try:
        gdi32.SelectObject(ctypes.c_void_p(memory), ctypes.c_void_p(bitmap))
        ok = bool(user32.PrintWindow(ctypes.c_void_p(hwnd),
                                     ctypes.c_void_p(memory), flags))
        return ok, _dib_image(memory, bitmap, size)
    finally:
        if bitmap:
            gdi32.DeleteObject(ctypes.c_void_p(bitmap))
        if memory:
            gdi32.DeleteDC(ctypes.c_void_p(memory))
        user32.ReleaseDC(None, ctypes.c_void_p(screen))


def _screen() -> Image.Image:
    """The interactive desktop, as the capture backend sees it.

    ``ImageGrab.grab(all_screens=True)`` is the same call the UIA backend
    makes; going to it directly keeps this probe runnable on a machine
    where the UIA backend (and pywinauto) is not installed.
    """
    from PIL import ImageGrab

    return ImageGrab.grab(all_screens=True).convert("RGB")


def _mismatch(painted: list[tuple[int, int]], expected: Image.Image,
              actual: Image.Image) -> int:
    """How many of the drawing's pixels the copy got wrong."""
    wanted = expected.load()
    got = actual.load()
    return sum(1 for x, y in painted if got[x, y] != wanted[x, y])


def _desktop_screen_bitblt(size: tuple[int, int],
                           origin: tuple[int, int]) -> tuple[bool, int]:
    """Can this desktop's screen be read at all?

    Asked through ``ctypes`` rather than pywin32 so that Windows' own
    error code comes back with the answer: a desktop that is not being
    shown has no screen surface, and the code says exactly that
    instead of leaving a bare ``BitBlt failed`` to argue with.
    """
    width, height = size
    x, y = origin
    user32, gdi32 = _apis()

    screen = user32.GetDC(None)
    if not screen:
        return False, ctypes.get_last_error()
    memory = gdi32.CreateCompatibleDC(ctypes.c_void_p(screen))
    bitmap = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(screen),
                                          width, height)
    try:
        gdi32.SelectObject(ctypes.c_void_p(memory), ctypes.c_void_p(bitmap))
        ok = bool(gdi32.BitBlt(ctypes.c_void_p(memory), 0, 0, width, height,
                               ctypes.c_void_p(screen), x, y, _SRCCOPY))
        return ok, ctypes.get_last_error()
    finally:
        if bitmap:
            gdi32.DeleteObject(ctypes.c_void_p(bitmap))
        if memory:
            gdi32.DeleteDC(ctypes.c_void_p(memory))
        user32.ReleaseDC(None, ctypes.c_void_p(screen))


def _opaque(image: Image.Image) -> list[tuple[int, int]]:
    alpha = image.getchannel("A").load()
    width, height = image.size
    return [(x, y) for y in range(height) for x in range(width)
            if alpha[x, y]]


# --------------------------------------------------------------------- probe
@dataclass
class Check:
    """One question, the answer, and the number it was decided on."""

    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}

    def line(self) -> str:
        return f"{self.status:4} {self.name}: {self.detail}"


@dataclass
class SecondDesktopReport:
    """What a second desktop did, measured, with nothing taken on trust."""

    desktop_name: str = ""
    created: bool = False
    error: str = ""
    hold_seconds: float = 0.0
    topology: Topology | None = None
    window: dict[str, Any] = field(default_factory=dict)
    foreground: dict[str, Any] = field(default_factory=dict)
    cursor: dict[str, Any] = field(default_factory=dict)
    capture: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    #: The desktop handle, closed from the probe's own thread: a desktop
    #: cannot be closed by a thread that is still attached to it.
    handle: int = field(default=0, repr=False)

    def status(self, name: str) -> str:
        for check in self.checks:
            if check.name == name:
                return check.status
        return SKIP

    def detail(self, name: str) -> str:
        for check in self.checks:
            if check.name == name:
                return check.detail
        return ""

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if check.status == FAIL]

    @property
    def overall(self) -> str:
        """PASS only when nothing failed and something was actually measured."""
        if self.failed:
            return FAIL
        if any(check.status == PASS for check in self.checks):
            return PASS
        return SKIP

    def to_dict(self) -> dict[str, Any]:
        return {
            "desktop": self.desktop_name,
            "created": self.created,
            "error": self.error,
            "overall": self.overall,
            "topology": self.topology.to_dict() if self.topology else None,
            "window": self.window,
            "foreground": self.foreground,
            "cursor": self.cursor,
            "capture": self.capture,
            "checks": [check.to_dict() for check in self.checks],
        }

    def lines(self) -> list[str]:
        """The report as text, numbers included."""
        out: list[str] = []
        if self.topology is not None:
            out += self.topology.summary()
        state = "created" if self.created else f"not created ({self.error})"
        out.append(f"second desktop {self.desktop_name!r}: {state}")
        if self.window:
            out.append(
                f"  window {self.window.get('hwnd')} "
                f"{self.window.get('size')} at {self.window.get('position')} "
                f"visible={self.window.get('visible')} "
                f"exstyle=0x{self.window.get('ex_style', 0):08x}")
            out.append(
                f"  region: complexity={self.window.get('region_complexity')} "
                f"box={self.window.get('region_box')} "
                f"painted_outside={self.window.get('painted_outside')} "
                f"blank_inside={self.window.get('blank_inside')}")
        if self.capture:
            out.append(
                f"  window DC: {self.capture.get('window_dc_matched')}"
                f"/{self.capture.get('painted_pixels')} painted pixels match")
            out.append(
                f"  PrintWindow: ok={self.capture.get('printwindow_ok')} "
                f"{self.capture.get('printwindow_matched')}"
                f"/{self.capture.get('painted_pixels')} pixels match")
            out.append(
                f"  desktop screen DC: {self.capture.get('screen_dc_result')}")
            out.append(
                f"  screen grab: {self.capture.get('screen_changed_pixels')} "
                f"pixels changed inside the window rect")
        if self.foreground:
            out.append(
                f"  foreground: before={self.foreground.get('before')} "
                f"mid={self.foreground.get('mid')} "
                f"after={self.foreground.get('after')} "
                f"(inside the second desktop: "
                f"{self.foreground.get('inside_desktop')})")
        if self.cursor:
            out.append(
                f"  cursor: before={self.cursor.get('before')} "
                f"mid={self.cursor.get('mid')} "
                f"after={self.cursor.get('after')}")
        out += [f"  {check.line()}" for check in self.checks]
        out.append(f"overall: {self.overall}")
        return out


def _desktop_name() -> str:
    return (f"OpenEyesSecondDesktop-{os.getpid()}-"
            f"{next(_COUNTER)}")


def _measure_on_desktop(report: SecondDesktopReport, ready: threading.Event,
                        release: threading.Event, options: dict[str, Any]) -> None:
    """Create the desktop, put the cursor on it, measure, then let it go.

    This runs on its own thread because ``SetThreadDesktop`` may only be
    called by a thread that owns no windows - which is exactly true of a
    fresh thread and false of the one that called us.
    """
    import win32con
    import win32gui

    from openeyes.agent.overlay import CursorOverlay

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateDesktopW.restype = ctypes.c_void_p
    user32.CreateDesktopW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.SetThreadDesktop.argtypes = [ctypes.c_void_p]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    user32.GetThreadDesktop.restype = ctypes.c_void_p
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    original = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())

    hdesk = user32.CreateDesktopW(report.desktop_name, None, None, 0,
                                  _DESKTOP_ALL_ACCESS, None)
    if not hdesk:
        report.error = f"CreateDesktop failed, GetLastError={ctypes.get_last_error()}"
        ready.set()
        return
    report.created = True
    report.handle = int(hdesk)
    if not user32.SetThreadDesktop(ctypes.c_void_p(hdesk)):
        report.error = (f"SetThreadDesktop failed, "
                        f"GetLastError={ctypes.get_last_error()}")
        # The main thread is waiting on this event; without it a refusal
        # would look like a 20-second hang instead of an error.
        ready.set()
        return

    overlay = CursorOverlay(agent_id=options["agent_id"],
                            label=options["label"], color=options["color"])
    try:
        x, y = options["position"]
        overlay.show(x, y)

        drawing = overlay.render()
        size = drawing.size
        painted = _opaque(drawing)
        hwnd = overlay._hwnd
        ex_style = int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE))

        # The window is cut to the silhouette: ask the region itself,
        # pixel by pixel, the way the interactive test does.
        region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
        complexity = int(win32gui.GetWindowRgn(hwnd, region))
        region_box = tuple(win32gui.GetRgnBox(region))
        painted_set = set(painted)
        blank = [(px, py) for py in range(size[1]) for px in range(size[0])
                 if (px, py) not in painted_set]
        painted_outside = sum(1 for px, py in painted
                              if not win32gui.PtInRegion(region, px, py))
        blank_inside = sum(1 for px, py in blank
                           if win32gui.PtInRegion(region, px, py))
        win32gui.DeleteObject(region)

        own = _window_dc_pixels(hwnd, size)
        expected = drawing.convert("RGB")
        own_matched = len(painted) - _mismatch(painted, expected, own)
        print_ok, printed = _print_window(hwnd, size)
        print_matched = len(painted) - _mismatch(painted, expected, printed)

        # A screen grab on this desktop: a desktop nobody is showing
        # has no screen surface to read, and says so with a code.
        ok, code = _desktop_screen_bitblt(size, (x, y))
        if ok:
            surface = _desktop_screen_pixels(size, (x, y))
            non_zero = int(np.count_nonzero(np.asarray(surface)))
            screen_result = (f"BitBlt succeeded, {non_zero} non-zero "
                             "channel values")
        else:
            screen_result = (f"BitBlt of the desktop screen DC failed, "
                             f"GetLastError={code}"
                             + (" (ERROR_INVALID_HANDLE)" if code == 6
                                else ""))

        report.window = {
            "hwnd": hwnd,
            "size": list(size),
            "position": [x, y],
            "visible": bool(win32gui.IsWindowVisible(hwnd)),
            "ex_style": ex_style,
            "region_complexity": complexity,
            "region_box": list(region_box),
            "painted_pixels": len(painted),
            "painted_outside": painted_outside,
            "blank_inside": blank_inside,
        }
        report.capture = {
            "window_dc_matched": own_matched,
            "printwindow_ok": print_ok,
            "printwindow_matched": print_matched,
            "painted_pixels": len(painted),
            "screen_dc_result": screen_result,
        }
        report.foreground["inside_desktop"] = int(
            win32gui.GetForegroundWindow() or 0)

        # The window stays up while the main thread takes its readings;
        # the deadline is only there so a lost handshake cannot wedge
        # this thread.
        ready.set()
        deadline = time.monotonic() + options["hold"] + 5.0
        while not release.is_set() and time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                win32gui.PumpWaitingMessages()
            time.sleep(0.01)
    finally:
        overlay.close()
        # Step off the desktop before this thread ends, and leave the handle
        # to be closed by the probe: a desktop whose thread is still
        # attached answers ERROR_BUSY (170) to ``CloseDesktop``, and a
        # thread that exits while attached keeps it busy for the process.
        with contextlib.suppress(Exception):
            user32.SetThreadDesktop(ctypes.c_void_p(original))


def _close_desktop(report: SecondDesktopReport) -> None:
    """Close the probe's desktop, now that its thread has exited.

    ``CloseDesktop`` answers 0 for a desktop the calling thread is still
    attached to, which is why this is not done on the measuring thread:
    the handle belongs to the process, and a thread that never attached
    to the desktop can close it once the one that did has finished.
    """
    if not report.handle:
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    if not user32.CloseDesktop(ctypes.c_void_p(report.handle)):
        note = f"CloseDesktop failed, GetLastError={ctypes.get_last_error()}"
        report.error = f"{report.error}; {note}" if report.error else note
    report.handle = 0


def probe_second_desktop(position: tuple[int, int] | None = None,
                         *, agent_id: str = "second-desktop",
                         label: str = "", color: str = "#00c853",
                         hold: float = 1.5,
                         desktop_name: str = "") -> SecondDesktopReport:
    """Create a second desktop, draw the cursor on it, and measure everything.

    Nothing is clicked, nothing is typed, the pointer is not moved and
    ``SwitchDesktop`` is never called - the person at the machine keeps
    looking at the desktop they were looking at.
    """
    report = SecondDesktopReport(desktop_name=desktop_name or _desktop_name(),
                                 hold_seconds=hold)
    if not sys.platform.startswith("win"):
        report.error = "not Windows"
        report.checks = [Check("second_desktop", SKIP, "not a Windows host")]
        return report

    report.topology = display_topology()
    work = report.topology.displays[0].work if report.topology.displays \
        else (0, 0, 800, 600)
    if position is None:
        position = (work[0] + 120, work[1] + 120)
    position = (min(position[0], work[2] - 60), min(position[1], work[3] - 60))

    import win32gui

    from openeyes.agent.overlay import CursorOverlay

    drawing = CursorOverlay(agent_id=agent_id, label=label,
                            color=color).render()
    # The colour a screen grab would have to show: the most common opaque
    # colour, which is the arrow's fill rather than its white outline.
    opaque = _opaque(drawing)
    rgb = Counter(drawing.getpixel(px)[:3] for px in opaque).most_common(1)[0][0]

    def pointer() -> tuple[int, int]:
        return tuple(int(value) for value in win32gui.GetCursorPos())

    foreground_before = int(win32gui.GetForegroundWindow() or 0)
    cursor_before = pointer()
    screen_before = _screen()

    ready = threading.Event()
    release = threading.Event()
    options = {"position": position, "agent_id": agent_id, "label": label,
               "color": color, "hold": hold}
    worker = threading.Thread(target=_measure_on_desktop,
                              args=(report, ready, release, options),
                              name="openeyes-second-desktop", daemon=True)
    worker.start()
    ready.wait(_READY_TIMEOUT)

    foreground_mid = int(win32gui.GetForegroundWindow() or 0)
    cursor_mid = pointer()
    screen_mid = _screen()
    release.set()
    worker.join(_READY_TIMEOUT)
    foreground_after = int(win32gui.GetForegroundWindow() or 0)
    cursor_after = pointer()
    _close_desktop(report)

    report.foreground.update(before=foreground_before, mid=foreground_mid,
                             after=foreground_after,
                             unchanged=(foreground_before == foreground_mid
                                        == foreground_after))
    report.cursor.update(before=list(cursor_before), mid=list(cursor_mid),
                         after=list(cursor_after),
                         unchanged=cursor_before == cursor_mid == cursor_after)

    if report.window:
        left, top = position
        width, height = report.window["size"]
        box = (left, top, left + width, top + height)
        changed = int(np.count_nonzero(
            np.any(np.asarray(screen_before.crop(box))
                   != np.asarray(screen_mid.crop(box)), axis=-1)))
        seen = int(np.count_nonzero(
            np.all(np.asarray(screen_mid.crop(box)) == np.array(rgb),
                   axis=-1)))
        report.capture["screen_changed_pixels"] = changed
        report.capture["screen_pixels_of_the_cursor_colour"] = seen
        report.capture["cursor_rgb"] = list(rgb)
        report.capture["screen_box"] = list(box)

    report.checks = _checks(report)
    return report


def _checks(report: SecondDesktopReport) -> list[Check]:
    """Turn the measurements into PASS/FAIL/SKIP, with the numbers attached."""
    checks: list[Check] = []
    if not report.created:
        others = report.error or "the desktop was not created"
        return [
            Check("second_desktop", SKIP, others),
            Check("window_created", SKIP, "no second desktop to create it on"),
            Check("region_cut", SKIP, "no second desktop"),
            Check("pixels_painted", SKIP, "no second desktop"),
            Check("window_captureable", SKIP, "no second desktop"),
            Check("screen_capture", SKIP, "no second desktop"),
            Check("foreground_unchanged", SKIP, "no second desktop"),
            Check("cursor_unchanged", SKIP, "no second desktop"),
        ]

    checks.append(Check("second_desktop", PASS,
                        f"{report.desktop_name!r} created and made this "
                        f"thread's desktop"))
    window = report.window
    visible = window.get("visible")
    checks.append(Check(
        "window_created",
        PASS if window and visible else FAIL,
        f"hwnd={window.get('hwnd')} size={window.get('size')} "
        f"visible={visible} exstyle=0x{window.get('ex_style', 0):08x}"))

    complexity = window.get("region_complexity")
    outside = window.get("painted_outside")
    inside = window.get("blank_inside")
    region_ok = (complexity == COMPLEXREGION and outside == 0 and inside == 0)
    checks.append(Check(
        "region_cut",
        PASS if region_ok else FAIL,
        f"GetWindowRgn complexity={complexity} "
        f"(COMPLEXREGION={COMPLEXREGION}) box={window.get('region_box')}, "
        f"painted pixels outside the region={outside}, "
        f"blank pixels inside={inside}"))

    capture = report.capture
    painted = capture.get("painted_pixels", 0)
    own = capture.get("window_dc_matched", 0)
    checks.append(Check(
        "pixels_painted",
        PASS if painted and own == painted else FAIL,
        f"{own}/{painted} painted pixels read back from the window's own DC "
        f"match the drawing"))

    printed_ok = capture.get("printwindow_ok")
    printed = capture.get("printwindow_matched", 0)
    checks.append(Check(
        "window_captureable",
        PASS if printed_ok and printed == painted else FAIL,
        f"PrintWindow(PW_RENDERFULLCONTENT) returned {printed_ok}, "
        f"{printed}/{painted} pixels match with nothing else painted"))

    changed = capture.get("screen_changed_pixels") or 0
    seen = capture.get("screen_pixels_of_the_cursor_colour") or 0
    if changed:
        screen_detail = (f"{changed} screen pixels changed inside the window "
                         f"rect - the second desktop is being composed "
                         f"somewhere")
        screen_status = PASS
    else:
        screen_detail = (
            "not visible on the interactive desktop, as expected: "
            f"{changed} screen pixels changed inside the window rect, "
            f"{seen} pixels carry the cursor colour "
            f"{capture.get('cursor_rgb')}, and this desktop's own screen DC "
            f"says {capture.get('screen_dc_result')}")
        screen_status = SKIP
    checks.append(Check("screen_capture", screen_status, screen_detail))

    foreground = report.foreground
    if not foreground.get("before"):
        foreground_status = SKIP
        foreground_detail = (
            "the interactive desktop had no foreground window to protect "
            f"(GetForegroundWindow before={foreground.get('before')} "
            f"mid={foreground.get('mid')} after={foreground.get('after')})")
    else:
        foreground_status = (PASS if foreground.get("unchanged") else FAIL)
        foreground_detail = (
            f"GetForegroundWindow before={foreground.get('before')} "
            f"mid={foreground.get('mid')} after={foreground.get('after')} "
            f"(the second desktop's own foreground is "
            f"{foreground.get('inside_desktop')})")
    checks.append(Check("foreground_unchanged", foreground_status,
                        foreground_detail))

    cursor = report.cursor
    checks.append(Check(
        "cursor_unchanged",
        PASS if cursor.get("unchanged") else FAIL,
        f"GetCursorPos before={cursor.get('before')} mid={cursor.get('mid')} "
        f"after={cursor.get('after')}"))
    return checks
