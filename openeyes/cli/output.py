"""One output contract for every ``eyes`` command.

An agent driving the CLI needs two things the plain text output cannot
give it: a **stable shape** to parse, and a **loud failure** it can read
and act on. The rules, borrowed from how the CLI-Anything harnesses
address agents:

* machine-readable output is JSON, always under one key-free top level
  (a list, or an object with predictable keys);
* a human table is a rendering of the same data, never a second source of
  truth;
* a failure is a non-zero exit code plus a message that names the cause -
  an agent must be able to correct itself from the message alone.

``--json`` is a global flag: ``eyes --json detect --window 1234`` and
``eyes detect --window 1234 --json`` both work, because a caller should
not have to remember which subcommand accepted the switch.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import click


def emit_json(payload: Any) -> None:
    """Print a payload as JSON on stdout."""
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def emit_error(message: str, *, code: int = 1, **extra: Any) -> None:
    """Report a failure in a shape an agent can act on.

    The message names the cause and the exit code is non-zero. The
    human-readable line goes to **stderr**; the JSON goes to **stdout**
    when JSON output was asked for, so stdout stays parseable - a caller
    that pipes stdout into a JSON parser must never receive an extra
    line.
    """
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    if _json_requested():
        emit_json(payload)
    click.echo(f"error: {message}", err=True)
    sys.exit(code)


def _json_requested() -> bool:
    """Whether the caller asked for JSON output."""
    ctx = click.get_current_context(silent=True)
    while ctx is not None:
        if ctx.params.get("as_json"):
            return True
        ctx = ctx.parent
    return False


def wants_json() -> bool:
    """Public form of :func:`_json_requested` for command bodies."""
    return _json_requested()


def render_table(headers: list[str], rows: list[list[Any]]) -> None:
    """Render a simple aligned table (the human view of the same data)."""
    if not rows:
        click.echo("  (none)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    click.echo("  " + line)
    click.echo("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        click.echo("  " + "  ".join(
            str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
