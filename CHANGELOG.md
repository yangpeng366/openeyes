# Changelog

All notable changes to OpenEyes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Quality gate.** `examples/quality-gate.ps1` runs lint, tests, the encoding
  sweep and the MCP stdio contract in one command, and `.github/workflows/ci.yml`
  runs the same four steps on every push to `main`. A push is ready when that
  script exits 0. `examples/bom-sweep.py` fails the build on a UTF-8 BOM, the
  defect an editor hides and a toolchain does not.
- `openeyes.core.dpi` — per-monitor-v2 DPI awareness, applied once and never
  downgraded from what the host process already set. Windows virtualises
  coordinates for DPI-unaware processes, so a reported bbox and the click that
  follows it could disagree; now every coordinate in the process is a real
  screen pixel.
- `move_to` / `cursor_pos` and a `press_ms` knob on `click_xy` — a single
  `SetCursorPos` followed by a click can be delivered at the *previous* cursor
  position, because the window has not processed `WM_MOUSEMOVE` yet; XAML/UWP
  controls can also ignore a zero-duration click.
- `click_element` and `automation_id` filtering — the stable control id beats
  a visible name that changes every second.
- `openeyes.core.verify` — `act_until(act, expect, retries=3)` and
  `wait_until(condition, timeout=5.0)`. A delivered click is not an effective
  click: AnyVPN's keepalive button carries a live countdown, so "the label
  changed" proves nothing and only the countdown jumping back up proves the
  session was renewed. `act_until` acts, polls the expectation, retries with
  the caller's next strategy when it did not take, and reports `{ok,
  attempts, result, elapsed, error}` — `ok` is True only when the expected
  state was observed.
- **App lifecycle primitives** (`openeyes.core.lifecycle`): `app_state`,
  `restore_window`, `launch_app` and `ensure_app`. A session timeout makes
  an app close its own window; "the window is gone" is not a diagnosis,
  because the process may still be alive (restore) or gone (relaunch).
  `app_state` tells `running` / `window-gone` / `not-running` apart with
  the evidence behind the verdict, and `ensure_app` follows the same rule
  as `act_until`: `ok` is true only when usability was **observed**, never
  merely because a launch command was issued. `restore_window` uses
  `SW_SHOWNOACTIVATE`, so a minimised window comes back with a populated
  accessibility tree without stealing the foreground.
- `eyes app state` / `eyes app ensure` CLI verbs, and the `app_ensure` MCP
  tool (tool surface 15 -> 16; `dry_run=true` by default). The AnyVPN
  keepalive example now uses these primitives instead of its own
  hard-coded window hunt, restore and relaunch.
- The decision layer can now run against a **local System One node** (a local
  XERJ node speaks the same wire): `TypesafeJevScorer` names each option by
  the key of `criteria`, as the protocol does, maps the answer back to a
  candidate index, and accepts the per-option support scores a history vote
  returns instead of a probability distribution.

### Changed
- `openeyes.backends.cdp.launch_edge` now captures Edge stderr to a temp file and surfaces both the early-exit return code and a bounded stderr tail in the `CDPError` message, so a failed CDP launch on a dedicated port (e.g. 9333) reports *why* Edge quit instead of only that it did. `_diagnose_launch` accepts optional `exit_code` and `stderr_tail` and the new `_read_stderr_tail` helper truncates very long output.
- `docs/architecture.md` documents the backend priority order, the degradation chain, and the coordinate semantics of the vision tier (image pixels are converted to window-relative coordinates through `origin=(dx, dy)`).
- `docs/capability-contract.md` records the MCP surface at 15 tools: the vision tier and the chain ship as CLI, Python, and MCP surfaces, and both MCP hosts can call the CLI.

### Added
- `openeyes.backends.vision` — a *parsing-style* vision backend. Screen-parsing detections (OmniParser v2 native shape, plus per-item dicts and `boxes`/`labels`/`scores` shapes) become the same `Element[]` shape the UIA and CDP backends emit, so the selector, hint and decision layers work unchanged. `OmniParserBackend` supports a model-free file mode (`detections_path=`) and an HTTP endpoint mode (`endpoint=`); both funnel through the same conversion, so results are identical. Vision elements carry `score < 1.0` so the decision layer can prefer structured backends, and only actionable labels are marked `interactive`.
- `openeyes.core.resolve` — the structured-first degradation chain `uia -> cdp -> vision`, now executable rather than a doc line. `resolve_elements()` returns a `ResolveResult` with the elements, the `used_backend`, and an `Attempt` per tier (ok / element count / failure reason), so a caller can explain why the chain fell through to a lower tier.
- `eyes vision --detections <json>` — CLI: convert screen-parsing detections into elements. No model and no network required.
- `eyes resolve --window <hwnd> [--port N] [--vision-detections <json>]` — CLI: walk the chain and report which tier answered plus the per-tier trail. Read-only; it never actuates.
- `openeyes.resolve_elements` / `ResolveResult` / `Attempt` are exported from the package root.
- `resolve_elements` (MCP) — walks the `uia -> cdp -> vision` chain and returns the first non-empty element set, the tier that answered, and the per-tier trail. Read-only; never actuates.
- `vision_detect` (MCP) — parses screen-parsing detections (file path or inline JSON) into `Element[]`. Read-only; no model runs on the host.
- The MCP tool set goes from 13 to 15. The change is additive: no tool was renamed or removed, no required argument changed, and no default execution mode changed. `docs/capability-contract.md` now pins 15 names, and `examples/mcp-stdio-probe.py` expects 15.
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