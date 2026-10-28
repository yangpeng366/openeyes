"""Tests for the packaged-application identity rules.

A Store / MSIX application almost never runs under its display name:
Any VPN shows up as ``VpnGui.exe`` inside WindowsApps, and its window
belongs to ``ApplicationFrameHost.exe``. Two consequences, both found by
running against the real application:

* a name-based process check reports the app as *not running* while its
  window is plainly on screen;
* a window filtered by that process name is never found, because the
  window does not belong to the process.

These tests pin the fixes.
"""
from __future__ import annotations

import pytest

from openeyes.backends import lifecycle as backend


# ------------------------------------------------------- package matching
@pytest.mark.parametrize("package,wanted,expected", [
    ("anywaysoftinc.anyvpn_1.0.8.0_x64__0qkrc2qacwvfm", "any vpn", True),
    ("anywaysoftinc.anyvpn_1.0.8.0_x64__0qkrc2qacwvfm", "anyvpn", True),
    ("anywaysoftinc.anyvpn_1.0.8.0_x64__0qkrc2qacwvfm", "vpn", True),
    ("anywaysoftinc.anyvpn_1.0.8.0_x64__0qkrc2qacwvfm", "someotherapp", False),
    ("microsoft.windowscalculator_1.0.0.0_x64__8wekyb3d8bbwe", "calculator", True),
])
def test_a_package_directory_matches_the_application_it_carries(package, wanted,
                                                               expected):
    assert backend._package_matches(package, wanted) is expected


def test_an_empty_package_never_matches():
    assert backend._package_matches("", "any vpn") is False
    assert backend._package_matches("anyvpn", "") is False


# ------------------------------------------------------------ window owner
@pytest.mark.parametrize("owner,expected", [
    ("applicationframehost", True),
    ("applicationframewindow", True),
    ("windows.ui.core.corewindow", True),
    ("vpnGui", False),
    ("notepad", False),
    ("", True),
])
def test_a_framework_host_may_own_the_window(owner, expected, monkeypatch):
    """A packaged app's window belongs to the host, not to the app."""
    monkeypatch.setattr(backend, "_process_name_of", lambda pid: owner)
    assert backend._owner_may_be("any vpn", 123) is expected


def test_the_owner_check_accepts_the_real_owner(monkeypatch):
    monkeypatch.setattr(backend, "_process_name_of", lambda pid: "AnyVPN")
    assert backend._owner_may_be("any vpn", 123) is True


# ----------------------------------------------------------------- state
def test_a_window_owned_by_the_framework_host_is_found(monkeypatch):
    """The window identity wins; the owning process is only a hint."""
    monkeypatch.setattr(backend, "_process_name_of",
                        lambda pid: "ApplicationFrameHost")
    monkeypatch.setattr(backend, "_package_of_window", lambda name: "")
    monkeypatch.setattr(backend, "_win32",
                        lambda: (_FakeWin32(), _FakeWin32Process()))

    found = backend.find_window(title_contains="Any VPN", process_name="Any VPN")
    assert found is not None
    assert found.title == "Any VPN"


class _FakeWin32:
    """Just enough of win32gui for the window enumeration."""

    @staticmethod
    def IsWindowVisible(_hwnd):
        return True

    @staticmethod
    def GetWindowText(_hwnd):
        return "Any VPN"

    @staticmethod
    def GetClassName(_hwnd):
        return "ApplicationFrameWindow"

    @staticmethod
    def EnumWindows(_callback, _extra):
        _callback(4242, None)

    @staticmethod
    def GetWindowRect(_hwnd):
        return (0, 0, 100, 100)

    @staticmethod
    def IsIconic(_hwnd):
        return False


class _FakeWin32Process:
    @staticmethod
    def GetWindowThreadProcessId(_hwnd):
        return (0, 28688)
