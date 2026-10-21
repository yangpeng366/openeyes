# Changelog

All notable changes to OpenEyes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- `openeyes.backends.cdp.launch_edge` now captures Edge stderr to a temp file and surfaces both the early-exit return code and a bounded stderr tail in the `CDPError` message, so a failed CDP launch on a dedicated port (e.g. 9333) reports *why* Edge quit instead of only that it did. `_diagnose_launch` accepts optional `exit_code` and `stderr_tail` and the new `_read_stderr_tail` helper truncates very long output.
- `docs/architecture.md` documents the backend priority order, the degradation chain, and the coordinate semantics of the vision tier (image pixels are converted to window-relative coordinates through `origin=(dx, dy)`).
- `docs/capability-contract.md` records that the MCP surface is intentionally unchanged at 13 tools: the vision tier and the chain ship as CLI and Python surfaces for now, and both MCP hosts can call the CLI.

### Added
- `openeyes.backends.vision` — a *parsing-style* vision backend. Screen-parsing detections (OmniParser v2 native shape, plus per-item dicts and `boxes`/`labels`/`scores` shapes) become the same `Element[]` shape the UIA and CDP backends emit, so the selector, hint and decision layers work unchanged. `OmniParserBackend` supports a model-free file mode (`detections_path=`) and an HTTP endpoint mode (`endpoint=`); both funnel through the same conversion, so results are identical. Vision elements carry `score < 1.0` so the decision layer can prefer structured backends, and only actionable labels are marked `interactive`.
- `openeyes.core.resolve` — the structured-first degradation chain `uia -> cdp -> vision`, now executable rather than a doc line. `resolve_elements()` returns a `ResolveResult` with the elements, the `used_backend`, and an `Attempt` per tier (ok / element count / failure reason), so a caller can explain why the chain fell through to a lower tier.
- `eyes vision --detections <json>` — CLI: convert screen-parsing detections into elements. No model and no network required.
- `eyes resolve --window <hwnd> [--port N] [--vision-detections <json>]` — CLI: walk the chain and report which tier answered plus the per-tier trail. Read-only; it never actuates.
- `openeyes.resolve_elements` / `ResolveResult` / `Attempt` are exported from the package root.
- `examples/dsh-gate-readiness.py` is a read-only HTTP 200 probe for the dsh web host at `http://127.0.0.1:3080/`. It never starts dsh, launches a browser, or issues a `browser_click` request, so it is safe for scheduled patrol rounds. It emits JSON with `ready`, `status_code`, `next_recheck`, and the next Section 4 action; exit code `0` = ready, `2` = not ready. Documented under `docs/dsh-web-acceptance.md` Section 2 and `examples/README.md`.

### Tests
- 33 new tests: `tests/test_vision_backend.py` (21), `tests/test_resolve.py` (9), `tests/test_cli_vision.py` (3). The full network-free suite is 234 passed / 2 skipped.
- 4 new `tests/test_cdp.py` cases cover early-exit surfacing, `_diagnose_launch` exit-code/stderr-tail inclusion, missing-file stderr tail, and long-output truncation.
- 3 new `tests/test_dsh_gate_readiness.py` cases lock the readiness probe's default gate URL / safe next action, the HTTP 200 path, and the non-200 path; the full network-free suite is 109 passed.

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