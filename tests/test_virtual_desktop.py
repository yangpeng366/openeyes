"""Tests for the second-desktop probe.

Two kinds of claim are checked here.

The first kind is arithmetic: a software display driver must be told
apart from a piece of glass, a report with a failed check must not call
itself a pass, and a report that could not run must say skipped rather
than invent a result. Those need no desktop.

The second kind needs a real desktop, so it is checked against the one
this process runs on: the topology Windows reports, a cursor window
created on a desktop made with ``CreateDesktop`` and painted there, and
the interactive desktop's foreground window and pointer position taken
before, during and after. If this machine refuses to create a desktop at
all - a session without the right to, say - the test skips and prints the
error code it got; it never turns a refusal into a pass.

The probe registers the cursor window class while running on the second
desktop, so the last test is a canary: after a second desktop has been
used, an ordinary overlay on the interactive desktop must still be
created, cut to its silhouette and shown.
"""
from __future__ import annotations

import contextlib
import ctypes
import json
import sys

import pytest

from openeyes.agent import virtual_desktop as probe
from openeyes.agent.overlay import CursorOverlay
from openeyes.agent.virtual_desktop import (
    FAIL,
    PASS,
    PHYSICAL,
    SKIP,
    VIRTUAL,
    Adapter,
    Check,
    Display,
    SecondDesktopReport,
    Topology,
    classify_adapter,
    display_topology,
    probe_second_desktop,
    virtual_screen,
)

WINDOWS = pytest.mark.skipif(not sys.platform.startswith("win"),
                             reason="desktops are a Windows thing")


def _desktop_names() -> list[str]:
    """Every desktop on this process's window station."""
    user32 = ctypes.windll.user32
    user32.GetProcessWindowStation.restype = ctypes.c_void_p
    user32.EnumDesktopsW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p]
    callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_wchar_p,
                                  ctypes.c_void_p)
    found: list[str] = []

    def collect(name, _lparam):
        found.append(name)
        return 1

    station = ctypes.c_void_p(user32.GetProcessWindowStation())
    user32.EnumDesktopsW(station, callback(collect), None)
    return found


# ------------------------------------------------------- what a driver is
def test_a_software_display_driver_is_called_virtual():
    """An IDD registers as a display adapter; its name is the giveaway."""
    assert classify_adapter("OrayIddDriver Device",
                            r"Root\OrayIddDriver") == VIRTUAL
    assert classify_adapter("Todesk Virtual Display Adapter",
                            r"Root\tdIdd") == VIRTUAL
    assert classify_adapter("Spacedesk Display Driver",
                            r"ROOT\SPACEDESK") == VIRTUAL


def test_a_pci_display_adapter_is_physical():
    assert classify_adapter(
        "AMD Radeon(TM) Graphics",
        r"PCI\VEN_1002&DEV_1638&SUBSYS_380717AA&REV_C5") == PHYSICAL
    assert classify_adapter("Intel(R) UHD Graphics 630",
                            r"PCI\VEN_8086&DEV_3E9B") == PHYSICAL


def test_a_root_enumerated_display_is_virtual_whatever_it_is_called():
    """No display is a piece of hardware if it is a root software device."""
    assert classify_adapter("Some Unnamed Adapter",
                            r"Root\WhateverIdd") == VIRTUAL


# --------------------------------------------------------- what is showing
def _topology() -> Topology:
    """A topology with one real display and a driver that is only installed."""
    display = Display(device=r"\\.\DISPLAY1", rect=(0, 0, 1920, 1080),
                      work=(0, 0, 1920, 1040), dpi=96, scale=100,
                      primary=True)
    glass = Adapter(device=r"\\.\DISPLAY1", description="A Real GPU",
                    device_id=r"PCI\VEN_1002&DEV_1638", flags=0x5,
                    monitors=["A Real Monitor"])
    installed = Adapter(device=r"\\.\DISPLAY2",
                        description="OrayIddDriver Device",
                        device_id=r"Root\OrayIddDriver", flags=0x0,
                        monitors=[""])
    return Topology(displays=[display], adapters=[glass, installed],
                    virtual_screen=(0, 0, 1920, 1080))


def test_an_installed_but_idle_virtual_display_is_not_a_monitor():
    topology = _topology()
    assert len(topology.displays) == 1
    assert len(topology.virtual_adapters) == 1
    assert topology.virtual_adapters[0].attached is False
    assert topology.active_virtual_displays == []
    assert topology.second_display is None


def test_an_attached_virtual_display_counts_as_driving_a_monitor():
    topology = _topology()
    topology.adapters[1].flags = 0x1
    assert [adapter.device for adapter in topology.active_virtual_displays] \
        == [r"\\.\DISPLAY2"]


def test_the_topology_says_what_it_counts():
    lines = _topology().summary()
    assert any("displays: 1 attached" in line for line in lines)
    assert any("1 virtual (0 driving a monitor)" in line for line in lines)
    assert any("OrayIddDriver Device [virtual, not attached]" in line
               for line in lines)


def test_the_topology_serialises_to_json():
    payload = json.dumps(_topology().to_dict())
    restored = json.loads(payload)
    assert restored["displays"][0]["dpi"] == 96
    assert restored["virtual_adapters"] == [r"\\.\DISPLAY2"]
    assert restored["active_virtual_displays"] == []


# ------------------------------------------------------- reading a verdict
def _report(*checks: Check) -> SecondDesktopReport:
    return SecondDesktopReport(desktop_name="probe", created=True,
                              checks=list(checks))


def test_a_report_whose_checks_all_passed_is_a_pass():
    report = _report(Check("window_created", PASS, ""),
                     Check("cursor_unchanged", PASS, ""))
    assert report.overall == PASS
    assert report.status("cursor_unchanged") == PASS


def test_one_failed_check_is_enough_to_fail_the_report():
    report = _report(Check("pixels_painted", FAIL, "0/152"),
                     Check("cursor_unchanged", PASS, ""))
    assert report.overall == FAIL
    assert [check.name for check in report.failed] == ["pixels_painted"]


def test_a_report_that_could_not_run_says_skipped():
    report = _report(Check("second_desktop", SKIP, "CreateDesktop refused"))
    assert report.overall == SKIP


def test_a_missing_check_reads_as_skipped_rather_than_passing():
    assert _report().status("screen_capture") == SKIP


def test_a_refused_desktop_skips_every_check_instead_of_guessing():
    report = SecondDesktopReport(desktop_name="probe", created=False,
                                 error="CreateDesktop failed, GetLastError=5")
    checks = probe._checks(report)
    assert len(checks) == 8
    assert all(check.status == SKIP for check in checks)
    assert report.status("foreground_unchanged") == SKIP


def _measured_report(foreground: dict) -> SecondDesktopReport:
    return SecondDesktopReport(
        desktop_name="probe", created=True,
        window={
            "hwnd": 9001, "size": [22, 22], "visible": True,
            "region_complexity": 3, "painted_outside": 0,
            "blank_inside": 0,
        },
        capture={
            "painted_pixels": 152, "window_dc_matched": 152,
            "printwindow_ok": True, "printwindow_matched": 152,
            "screen_changed_pixels": 0,
        },
        foreground=foreground,
        cursor={"before": [1, 2], "mid": [1, 2], "after": [1, 2],
                "unchanged": True},
    )


def test_an_external_focus_change_is_not_attributed_to_the_probe():
    report = _measured_report({
        "before": 111, "mid": 222, "after": 333,
        "inside_desktop": 0, "unchanged": False,
    })
    assert probe._checks(report)[6].status == SKIP


def test_the_second_desktops_window_reaching_the_interactive_desktop_fails():
    report = _measured_report({
        "before": 111, "mid": 9001, "after": 333,
        "inside_desktop": 9001, "unchanged": False,
    })
    assert probe._checks(report)[6].status == FAIL


# ----------------------------------------------------- the real machine
@WINDOWS
def test_the_topology_is_the_machine_it_runs_on():
    topology = display_topology()
    assert topology.displays, "EnumDisplayMonitors reported no display at all"
    assert topology.adapters, "EnumDisplayDevices reported no adapter"
    assert topology.virtual_screen[2] > 0 and topology.virtual_screen[3] > 0
    for display in topology.displays:
        left, top, right, bottom = display.rect
        assert right > left and bottom > top
        assert display.dpi is None or display.dpi >= 96
    print("topology: " + " | ".join(topology.summary()))
    print(f"virtual screen: {virtual_screen()}")


@WINDOWS
def test_a_second_desktop_holds_a_cursor_window_without_touching_this_one():
    """The window is painted there; the pointer and foreground stay here."""
    before = set(_desktop_names())
    report = probe_second_desktop(position=(300, 300), label="", hold=0.4)
    if not report.created:
        pytest.skip(f"this session cannot create a desktop: {report.error}")

    for line in report.lines():
        print(line)

    assert report.overall != FAIL, "\n".join(
        line for line in report.lines() if line.startswith("  "))
    assert report.status("window_created") == PASS
    assert report.status("region_cut") == PASS
    assert report.status("pixels_painted") == PASS
    assert report.status("window_captureable") == PASS
    assert report.status("cursor_unchanged") == PASS
    assert report.status("foreground_unchanged") in (PASS, SKIP)

    # The window is painted there, and invisible from here: those are two
    # different questions and the report answers them separately.
    assert report.capture["painted_pixels"] > 0
    assert report.status("screen_capture") in (PASS, SKIP)
    if report.status("screen_capture") == SKIP:
        assert report.capture["screen_changed_pixels"] == 0

    # The desktop was closed, not left behind on somebody's window station.
    assert report.desktop_name not in _desktop_names()
    assert set(_desktop_names()) == before


@WINDOWS
def test_the_interactive_desktop_still_draws_after_the_probe():
    """The canary: the cursor window class is still usable here."""
    import win32gui

    from openeyes.core.dpi import ensure_dpi_awareness

    ensure_dpi_awareness()
    topology = display_topology()
    left, top = topology.displays[0].work[0] + 80, topology.displays[0].work[1] + 80
    overlay = CursorOverlay(agent_id="canary")
    try:
        assert overlay.show(left, top)
        hwnd = overlay._hwnd
        assert win32gui.IsWindow(hwnd)
        assert win32gui.IsWindowVisible(hwnd)
        region = win32gui.CreateRectRgnIndirect((0, 0, 0, 0))
        try:
            assert win32gui.GetWindowRgn(hwnd, region) == probe.COMPLEXREGION
        finally:
            win32gui.DeleteObject(region)
        rect = win32gui.GetWindowRect(hwnd)
        assert rect[2] - rect[0] == overlay.render().size[0]
        assert rect[3] - rect[1] == overlay.render().size[1]
    finally:
        with contextlib.suppress(Exception):
            overlay.hide()
        overlay.close()
