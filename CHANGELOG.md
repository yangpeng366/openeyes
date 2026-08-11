# Changelog

All notable changes to OpenEyes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-08-11

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