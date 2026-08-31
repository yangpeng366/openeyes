# Changelog

All notable changes to OpenEyes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- `openeyes.backends.cdp.launch_edge` now captures Edge stderr to a temp file and surfaces both the early-exit return code and a bounded stderr tail in the `CDPError` message, so a failed CDP launch on a dedicated port (e.g. 9333) reports *why* Edge quit instead of only that it did. `_diagnose_launch` accepts optional `exit_code` and `stderr_tail` and the new `_read_stderr_tail` helper truncates very long output.

### Tests
- 4 new `tests/test_cdp.py` cases cover early-exit surfacing, `_diagnose_launch` exit-code/stderr-tail inclusion, missing-file stderr tail, and long-output truncation; the full network-free suite is 106 passed.

## [0.1.0] - 2026-08-11

GitHub: <https://github.com/yangpeng366/openeyes>

### Added
- `openeyes-core`: cross-platform capture / detect / click primitives
- `openeyes.backends.uia`: Windows UIA backend via pywinauto
- `openeyes.actuators.win32`: Windows mouse / keyboard input via pywin32
- `openeyes-cli`: `eyes` command with subcommands `windows`, `capture`, `detect`, `click`, `grid`, `hotkey`, `type`
- `openeyes-mcp`: MCP server exposing 7 tools (`list_windows`, `capture_window`, `detect_elements`, `click`, `grid`, `hotkey`, `type_text`)
- Element schema: typed dataclass with `bbox`, `center`, `control_type`, `name`, `automation_id`, `class_name`, `state`, `parent_chain`
- Showcase: `examples/feishu_first_test.py` (飞书 client first test)
- Showcase: `examples/anyvpn_keepalive.py` (carry-over from NVK MVP)
- Plugin manifest: `.codex-plugin/plugin.json`
- Codex skill: `skills/openeyes/SKILL.md`
- Smoke tests: `tests/test_smoke.py`
- MIT LICENSE

### Notes
- This is the MVP. Vision backend (OmniParser/Florence) is on the v0.2.0 roadmap.
- Default click behavior is **dry-run** — pass `--go` to actually click.
- Refactored from NVK MVP (`E:\AI-Portable\codex-home\plugins\nvk\`). NVK stays as legacy plugin.
- Live at https://github.com/yangpeng366/openeyes
- First showcase validated against Feishu client (hwnd 331812, 317 elements) and AnyVPN UWP (hwnd 862936).