# Contributing to OpenEyes

Thanks for considering a contribution!

## Ground rules

- MIT-licensed. By submitting a patch you agree to license it under MIT.
- Code of conduct: be respectful. No spam, no harassment.
- Discussions happen in GitHub Discussions (TBD) or weekly chat (TBD).
- Open an issue before substantial work, so we can align on direction.

## Development setup

```powershell
git clone https://github.com/openeyes-ai/openeyes.git
cd openeyes
pip install -e ".[windows,mcp,dev]"

# run tests
pytest

# run CLI
eyes --help
```

## Code style

- Python 3.10+ (we use `from __future__ import annotations`)
- `ruff` for lint / format (config in `pyproject.toml`)
- Type hints on every public function
- No `print` in library code — use `logging` or return values
- CLI / MCP tools: every click defaults to `dry_run=True`

## Project layout

| Path | Purpose |
|---|---|
| `openeyes/core/` | Cross-platform capture / detect / actuate facade |
| `openeyes/backends/` | Platform backends (`uia`, planned `ax`, `atspi`) |
| `openeyes/actuators/` | Platform input backends (`win32`, planned `cg`, `xtest`) |
| `openeyes/cli/` | `eyes` CLI |
| `openeyes/mcp/` | `eyes-mcp` MCP server |
| `tests/` | `pytest` suite |
| `examples/` | Runnable showcases |
| `skills/openeyes/SKILL.md` | Codex skill |

## Adding a new backend

1. Create `openeyes/backends/<platform>.py` with `list_windows`,
   `capture_window`, `capture_screen`, `detect_elements`.
2. Update `openeyes/core/windows.py::_platform_backend` to dispatch.
3. Add a `pytest -m <platform>` marker so CI can filter.
4. Update README + architecture docs.

## Adding a new actuator

1. Create `openeyes/actuators/<platform>.py` with `click_xy`, `send_hotkey`,
   `type_text`, `drag`, `scroll`.
2. Update `openeyes/core/actuator.py::_backend` to dispatch.
3. Same as above.

## Commit messages

Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).