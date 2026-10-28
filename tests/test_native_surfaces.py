"""Tests for the native-surface discovery.

The point of this layer is to find the application's *own* way to be
driven - a command line, a URL scheme, a registered executable, a Store
AUMID - before anyone starts clicking pixels.

Two rules matter most and are pinned here:

* **A fake path is worse than no path.** The shell reports some
  applications as an opaque id; handing that to an agent as a runnable
  command would be a lie it would act on.
* **"Nothing found" must be distinguishable from "nothing looked
  for."** The report lists what it probed.
"""
from __future__ import annotations

import sys

import pytest

from openeyes.core import native


# ------------------------------------------------------------- the shapes
def test_a_cli_next_to_its_gui_is_found(tmp_path, monkeypatch):
    """7z.exe lives beside 7zFM.exe and is usually not on PATH."""
    (tmp_path / "7z.exe").write_bytes(b"")
    monkeypatch.setattr(native, "_gui_directories",
                        lambda names: [str(tmp_path)])
    surface = native.find_cli_surface(["7-Zip"], ["7z"])
    assert surface is not None
    assert surface.kind == native.SURFACE_CLI
    assert surface.target == str(tmp_path / "7z.exe")
    assert surface.evidence["found_by"] == "gui-directory"


def test_a_cli_on_path_is_found(monkeypatch):
    monkeypatch.setattr(native, "_gui_directories", lambda names: [])
    monkeypatch.setattr(native, "_run_where",
                        lambda name: r"C:\bin\tool.exe" if name == "tool" else "")
    surface = native.find_cli_surface(["Tool"], ["tool"])
    assert surface is not None
    assert surface.kind == native.SURFACE_CLI
    assert surface.evidence["found_by"] == "path"


def test_no_cli_is_reported_as_absent(monkeypatch):
    monkeypatch.setattr(native, "_gui_directories", lambda names: [])
    monkeypatch.setattr(native, "_run_where", lambda name: "")
    assert native.find_cli_surface(["Tool"], ["tool"]) is None


# -------------------------------------------------------------- path shape
def test_an_opaque_id_is_not_presented_as_a_path():
    assert native._looks_like_path("{308046B0AF4A39CB}") is False
    assert native._looks_like_path("") is False


def test_a_real_absolute_path_is_accepted():
    assert native._looks_like_path(sys.executable) is True


def test_a_relative_path_is_not_accepted():
    assert native._looks_like_path("tools\\thing.exe") is False


# ------------------------------------------------------------- the report
def test_the_report_lists_what_was_probed(monkeypatch):
    monkeypatch.setattr(native, "find_cli_surface", lambda *a, **k: None)
    monkeypatch.setattr(native, "find_url_scheme", lambda scheme: None)
    monkeypatch.setattr(native, "find_app_path", lambda name: None)
    monkeypatch.setattr(native, "find_msix_surface", lambda name: None)
    report = native.discover("Nothing", schemes=["nothing"],
                             exe_names=["nothing.exe"])
    assert report.found is False
    assert "cli" in report.probed
    assert "url-scheme:nothing" in report.probed
    assert "app-path:nothing.exe" in report.probed
    assert "msix" in report.probed
    assert "drive it through the window" in report.detail


def test_the_report_carries_the_surface(monkeypatch):
    surface = native.NativeSurface(kind=native.SURFACE_CLI,
                                   target="C:\\x\\tool.exe",
                                   command='"C:\\x\\tool.exe"')
    monkeypatch.setattr(native, "find_cli_surface",
                        lambda *a, **k: surface)
    monkeypatch.setattr(native, "find_msix_surface", lambda name: None)
    report = native.discover("Thing")
    assert report.found is True
    payload = report.to_dict()
    assert payload["count"] == 1
    assert payload["surfaces"][0]["kind"] == "cli"
    assert payload["surfaces"][0]["command"] == '"C:\\x\\tool.exe"'


# ------------------------------------------------------ name similarity
@pytest.mark.parametrize("stem,expected", [
    ("7z", True),
    ("7-Zip", True),
    ("yuanbao", False),
    ("uninstall", False),
    ("unins000", False),
    ("crashreporter", False),
    ("updater", False),
])
def test_auxiliary_executables_are_never_the_application(stem, expected):
    """7z matches 7-Zip; a helper never does."""
    assert native._similar("7-Zip", stem) is expected


def test_a_non_latin_name_matches_its_latin_executable():
    assert native._similar("元宝", "yuanbao") is True


def test_a_non_latin_name_needs_a_distinctive_latin_stem():
    assert native._similar("元宝", "yuanbao") is True
    assert native._similar("元宝", "ab") is False
    assert native._similar("元宝", "tool1") is False


# ------------------------------------------------------------- the shell
def test_the_shell_reports_aumid_and_path(monkeypatch):
    monkeypatch.setattr(native, "_shell_apps", lambda: [
        ("Any VPN", "AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App"),
        ("Firefox", r"C:\Program Files\Mozilla Firefox\firefox.exe"),
    ])
    aumid = native.find_msix_surface("Any VPN")
    assert aumid is not None
    assert aumid.kind == native.SURFACE_MSIX
    assert "shell:AppsFolder" in aumid.command

    firefox = native.find_msix_surface("Firefox")
    assert firefox is not None
    assert firefox.kind == native.SURFACE_APP_PATH


def test_an_opaque_shell_entry_gets_no_runnable_command(monkeypatch):
    monkeypatch.setattr(native, "_shell_apps",
                        lambda: [("Odd", "{308046B0AF4A39CB}")])
    monkeypatch.setattr(native, "find_app_path", lambda name: None)
    monkeypatch.setattr(native, "_gui_directories", lambda names: [])
    surface = native.find_msix_surface("Odd")
    assert surface is not None
    assert surface.command == ""
    assert surface.evidence.get("opaque") is True


def test_the_shell_lookup_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(native, "_shell_apps",
                        lambda: [("Any VPN", "AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App")])
    assert native.find_msix_surface("any vpn") is not None


def test_an_unknown_application_is_not_invented(monkeypatch):
    monkeypatch.setattr(native, "_shell_apps", lambda: [("Other", "x")])
    assert native.find_msix_surface("Nothing Here") is None
