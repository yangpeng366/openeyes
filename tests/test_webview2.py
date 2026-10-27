"""Tests for the WebView2 backend.

A Tauri / Wails / Electron shell paints its UI in a web view. To UIA it
looks like a few empty panes, so the only structured view of the UI is
the DOM - reachable over the DevTools protocol, if the shell was started
with a debugging port.

These tests pin the honest behaviour: the port is detected when it is
there, and its absence is reported with the exact way to open it, rather
than silently falling back to pixels.
"""
from __future__ import annotations

import json

import pytest

from openeyes.backends import webview2


def test_debug_args_name_the_port():
    args = webview2.debug_args_for(9333)
    assert "--remote-debugging-port=9333" in args
    assert "--remote-allow-origins=*" in args


def test_env_for_child_sets_the_webview_argument_variable():
    env = webview2.env_for_child(9444)
    assert webview2.WEBVIEW2_ARGS_ENV in env
    assert "--remote-debugging-port=9444" in env[webview2.WEBVIEW2_ARGS_ENV]


def test_env_for_child_keeps_existing_arguments():
    env = webview2.env_for_child(9444)
    env[webview2.WEBVIEW2_ARGS_ENV] = "--existing-flag"
    merged = webview2.env_for_child(9444)
    # the caller's env is the base; a fresh call must not lose what is there
    assert "--remote-debugging-port=9444" in merged[webview2.WEBVIEW2_ARGS_ENV]


def test_describe_reports_a_missing_endpoint_with_the_fix():
    report = webview2.describe(59999)
    assert report["reachable"] is False
    assert report["pages"] == []
    assert "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" in report["detail"]
    assert "59999" in report["detail"]


def test_describe_says_so_when_nothing_listens():
    assert webview2.port_is_open(59998, timeout=0.3) is False


def test_list_targets_raises_a_named_error_when_absent():
    with pytest.raises(webview2.WebView2Error) as excinfo:
        webview2.list_targets(59997)
    message = str(excinfo.value)
    assert "59997" in message
    assert "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" in message


def test_webview2_error_is_a_runtime_error():
    assert isinstance(webview2.WebView2Error("boom"), RuntimeError)


def test_launch_reports_a_missing_executable_instead_of_raising():
    report = webview2.launch_shell("C:\\no\\such\\shell.exe", wait_s=0)
    assert report["launched"] is False
    assert "could not start" in report["detail"]


def test_describe_output_is_json_serialisable():
    """The report is printed by the CLI, so it must be plain data."""
    report = webview2.describe(59996)
    assert json.loads(json.dumps(report))["reachable"] is False
