"""Tests for the trust metrics: false positives and interference.

The point of these numbers is that a loop which lives on someone's
desktop can be *judged*: how often it claims a success that did not
happen, and how often it disturbs the human.

The attribution rule matters as much as the arithmetic. On a shared
desktop the user's cursor keeps moving on its own and the focus may
already sit on the target window, so a naive before/after comparison
charges the automation for the user's own hand. These tests pin the
correct attribution.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "examples" / "measure-interference.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_interference", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mi():
    return _load()


def test_false_positive_rate_is_zero_when_every_success_was_real(mi):
    metrics = mi.RunMetrics(rounds=4, true_positives=4, false_positives=0)
    assert metrics.false_positive_rate == 0.0


def test_false_positive_rate_counts_only_reported_successes(mi):
    """A round that never reported success is not a false positive."""
    metrics = mi.RunMetrics(rounds=4, true_positives=3, false_positives=1,
                            failures=0)
    assert metrics.false_positive_rate == pytest.approx(0.25)


def test_false_positive_rate_is_zero_without_reported_successes(mi):
    metrics = mi.RunMetrics(rounds=3, true_positives=0, false_positives=0,
                            failures=3)
    assert metrics.false_positive_rate == 0.0


def test_interference_rate_counts_disturbed_rounds(mi):
    metrics = mi.RunMetrics(rounds=4)
    metrics.detail = [
        {"cursor_moved": False, "focus_stolen": False},
        {"cursor_moved": True, "focus_stolen": False},
        {"cursor_moved": False, "focus_stolen": True},
        {"cursor_moved": False, "focus_stolen": False},
    ]
    assert metrics.interference_rate == pytest.approx(0.5)


def test_interference_rate_is_zero_without_rounds(mi):
    assert mi.RunMetrics().interference_rate == 0.0


def test_metrics_serialise_every_number_a_report_needs(mi):
    metrics = mi.RunMetrics(rounds=2, true_positives=1, false_positives=1)
    payload = metrics.to_dict()
    for key in ("rounds", "true_positives", "false_positives", "failures",
                "false_positive_rate", "interference_rate", "cursor_moves",
                "focus_thefts", "foreground_changes", "detail"):
        assert key in payload


def test_the_script_documents_the_measured_comparison(mi):
    """The measured numbers belong in the file, not only in a chat log."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "100% false positives" in text
    assert "0% false positives" in text
