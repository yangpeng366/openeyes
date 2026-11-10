"""Body of the ``record_replay`` MCP tool.

Kept out of :mod:`openeyes.mcp.server` so it carries no MCP dependency: the
server is an optional install (``pip install openeyes[mcp]``), while the
payload shape below must stay unit-testable on its own. ``server.py`` only
registers the tool and wraps this function's return value.

The payload is the replayer report, unchanged, plus two fields that make the
outcome readable without walking the action list:

``ok``            True only when no step failed. It says nothing about
                  whether an action took effect - a step with no recorded
                  expectation is ``unverified``, and ``ok`` stays True for it.
``failed_steps``  index / action / reason / error of every failed step,
                  copied from ``actions`` rather than replacing it.

``live`` is opt-in and defaults to False. A dry run sends no input and claims
nothing; only ``live=true`` actuates the desktop. Per step,
``sent=True`` means delivered, never "the action took effect".
"""
from __future__ import annotations

from typing import Any

from openeyes.record.event import Trajectory
from openeyes.record.replayer import Replayer


def _positive_int(arguments: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(arguments.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(1, value)


def _positive_float(arguments: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(arguments.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(0.0, value)


def record_replay_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    """Replay one trajectory file and return a JSON-ready report.

    Raises ``ValueError`` when no path was given and whatever
    ``Trajectory.load`` raises for an unreadable file, so the caller can turn
    both into its own error envelope instead of a half-filled report.
    """
    path = arguments.get("trajectory_path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("trajectory_path is required")
    live = bool(arguments.get("live", False))

    trajectory = Trajectory.load(path.strip())
    report = Replayer(
        dry_run=not live,
        retries=_positive_int(arguments, "retries", 2),
        timeout=_positive_float(arguments, "timeout", 2.0),
        interval=_positive_float(arguments, "interval", 0.1),
    ).replay(trajectory)

    payload = report.to_dict()
    payload["ok"] = payload["summary"]["failed"] == 0
    payload["failed_steps"] = [
        {
            "index": action.get("index"),
            "action": action.get("action"),
            "reason": action.get("reason"),
            "error": action.get("error"),
        }
        for action in payload["actions"]
        if action.get("status") == "failed"
    ]
    return payload
