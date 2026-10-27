"""Tests for the accessibility-tree depth default.

Yuanbao (a Tauri app) taught this the hard way: its send button sits
around depth 20, so the old default of 12 could not see it. Every
id-anchored lookup missed, and `act_and_verify` reported a miss with no
elements in the evidence - which looks like "the control does not exist"
rather than "we did not look deep enough".

The default must therefore reach a web surface, and callers must still
be able to ask for a shallower or deeper walk.
"""
from __future__ import annotations

from openeyes.backends import uia
from openeyes.core import selector


def test_default_depth_reaches_a_web_surface():
    assert uia.DEFAULT_MAX_DEPTH >= 25


def test_core_detect_uses_the_backend_default(monkeypatch):
    seen = {}

    def fake_detect(hwnd, *, restore=False, max_depth=0):
        seen["max_depth"] = max_depth
        return []

    monkeypatch.setattr(uia, "detect_elements", fake_detect)
    selector.detect_elements(1234)
    assert seen["max_depth"] == uia.DEFAULT_MAX_DEPTH


def test_an_explicit_depth_is_passed_through(monkeypatch):
    seen = {}

    def fake_detect(hwnd, *, restore=False, max_depth=0):
        seen["max_depth"] = max_depth
        return []

    monkeypatch.setattr(uia, "detect_elements", fake_detect)
    selector.detect_elements(1234, max_depth=7)
    assert seen["max_depth"] == 7


def test_the_backend_default_is_the_one_documented():
    """The signature default must equal the exported constant."""
    import inspect
    signature = inspect.signature(uia.detect_elements)
    assert signature.parameters["max_depth"].default == uia.DEFAULT_MAX_DEPTH
