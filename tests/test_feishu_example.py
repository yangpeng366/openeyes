"""Tests for the Feishu first-run example CLI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "feishu_first_test.py"


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("feishu_first_test", EXAMPLE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def example(monkeypatch) -> ModuleType:
    module = _load_example()
    monkeypatch.setattr(module, "pick_hwnd", lambda title_contains="飞书": None)
    return module


def _run(example: ModuleType, monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["feishu_first_test.py", *argv])
    return example.main()


def test_dry_run_flag_is_accepted(example, monkeypatch, capsys):
    assert _run(example, monkeypatch, ["--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "no window matches" in err
    assert "unrecognized arguments" not in err


def test_no_arguments_defaults_to_dry_run(example, monkeypatch, capsys):
    assert _run(example, monkeypatch, []) == 2
    err = capsys.readouterr().err
    assert "no window matches" in err
    assert "unrecognized arguments" not in err


def test_go_and_dry_run_are_mutually_exclusive(example, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["feishu_first_test.py", "--go", "--dry-run"])
    with pytest.raises(SystemExit) as excinfo:
        example.main()
    assert excinfo.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
