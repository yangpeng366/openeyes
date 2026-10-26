---
name: openeyes
description: Use OpenEyes (eyes CLI / openeyes Python package / openeyes-mcp server) to see and operate any desktop UI on Windows/macOS/Linux. Includes UIA-backed native apps (Win32/UWP), CDP-backed Chromium browser pages (Edge/Chrome via DevTools Protocol — no screenshot, no vision model, structured DOM), Vimium-style letter hints, Vimium-style 3x3 grid, focus/click/type/hotkey/drag/screenshot. Use when Codex needs to (a) drive a native app without an API or DOM, (b) drive a real browser session with login cookies / 2FA, or (c) read or write page elements (text input, button click) without spending image-budget. Browser path uses raw websocket-client on Chrome DevTools Protocol; a parsing-style Vision backend (OmniParser v2 compatible) covers canvas / image-only UIs, and a structured-first chain (uia -> cdp -> vision) picks the cheapest tier that answers. Coordinates are DPI-correct (per-monitor-v2 awareness) and clicks settle before they fire, so a click lands on the element that was found; the decision layer can run against a local System One node instead of a hosted key.
---

# OpenEyes Skill

> Repo: <https://github.com/yangpeng366/openeyes> · Live at github.com/yangpeng366/openeyes

OpenEyes is the AI computer-use primitive layer on this machine. It replaces
hardcoded mouse coordinates with **structured detection**: enumerate
interactive elements, find the one matching the user's intent, click its
center. LLM never has to read pixels.

## Four backends, one schema

1. **UIA** (`openeyes.backends.uia`) — Windows / macOS / Linux native apps.
   pywinauto + ctypes. Free, no model. Covers Any VPN, VS Code, Feishu,
   Word, Excel, Explorer.
2. **CDP** (`openeyes.backends.cdp`) — Chromium browser pages (Edge / Chrome
   / WebView2). Raw websocket-client against Chrome DevTools Protocol.
   No LLM vision needed, no model on disk. The DOM probe returns structured
   ``Element[]`` with name, role, viewport-relative bbox, automation_id,
   class, parent chain. Pluggable in-page JS probe filters zero-area and
   ``display:none``; viewport-relative centers work directly via
   ``Input.dispatchMouseEvent`` / ``Input.insertText``. Falls back to
   ``seed_user_data`` profile copy so cookies survive across launches.
3. **Vision** (`openeyes.backends.vision`) — parsing-style fallback for
   canvas / image-only UIs. The screen is *parsed* into the same ``Element[]``
   shape the other backends emit (OmniParser v2 compatible), so nothing
   downstream clicks raw pixels. Two modes: model-free file mode
   (``detections_path=``) and HTTP endpoint mode (``endpoint=``).
4. **Lightpanda** (`openeyes.backends.lightpanda`) — headless Chromium over
   CDP, ~123 MB RAM, for unattended bulk extraction. Same DOM path.

All backends emit the same ``Element`` schema
(``backend / control_type / name / bbox / center / automation_id / hint
...``) so CLI / MCP / hints module are backend-agnostic.

## Quick commands

### Desktop / native (UIA)

```bash
# 1. find hwnd
eyes windows list --title-contains "飞书"

# 2. capture
eyes capture --window <hwnd> --out shot.png

# 3. detect (dry-run is automatic)
eyes detect --window <hwnd> --pretty

# 4. click by name (dry-run by default; pass --go to actually click)
eyes click --window <hwnd> --name-contains "消息" --go

# 5. Vimium-style grid (3x3 default)
eyes grid --window <hwnd> --row 1 --col 2 --go

# 6. hotkey / type
eyes hotkey --combo ctrl+a
eyes type --text "hello"
```

### Browser (CDP)

```bash
# 1. launch a dedicated Edge with --remote-debugging-port=9222 (--no-seed to skip cookie copy)
eyes browser launch --url https://example.com --no-seed

# 2. list tabs (HTTP /json discovery)
eyes browser tabs

# 3. DOM probe → JSON (--pretty for human view)
eyes browser scan --pretty --url-contains example.com

# 4. read first input (hint 'a' in reading order) — dry-run by default
eyes browser type --text "search term" --hint a

# 5. actually click the GO button at hint 's'
eyes browser click --hint s --go

# 6. snapshot viewport
eyes browser shot --out shot.png
```

The browser path also assigns Vimium-style letter hints to every
interactive element (``a``, ``s``, ``d``, ``f``, ... up to 2-letter,
3-letter up to 1884 total). An LLM can answer "click element ``s``"
without describing coordinates — same UX as UIA mode.

## MCP server

`eyes-mcp` exposes 15 primitives as MCP tools:

**Native (UIA):**
- `list_windows`
- `capture_window` (dry_run=true default; set false to write PNG)
- `detect_elements`
- `click` (dry_run=true default)
- `grid`
- `hotkey` (dry_run=true default; set false to send keys)
- `type_text` (dry_run=true default; set false to type)

**Browser (CDP):**
- `browser_launch`  --url / --port / --no-seed / --dry-run=false
- `browser_tabs`
- `browser_scan`    --pretty / --control-type / --name-contains
- `browser_click`   --hint / --idx / --name-contains / --go
- `browser_type`    --text / --hint / --idx / --enter / --dry-run=false
- `browser_shot`    --out / --dry-run=false

**Structured-first (read-only, v0.2.0):**
- `resolve_elements`  --hwnd / --port / --url-contains / --detections_path
- `vision_detect`     --detections_path / --detections / --origin-x / --origin-y

Start: `eyes-mcp` (stdio transport).

MCP side-effecting tools default to `dry_run=true`; explicitly pass
`dry_run=false` before writing files, launching a browser, sending keys, or
typing text. `browser_type` may scan the DOM in dry-run mode when a selector
is supplied, but it never focuses, types, or presses Enter until execution is
explicitly enabled.

## Native tier: coordinates and clicks that land

Windows virtualises coordinates for DPI-unaware processes: UI Automation
reports *physical* pixels while `GetWindowRect` and `SetCursorPos` stop
agreeing, and a click ends up a few pixels off - or on the wrong element.
`openeyes.core.dpi.ensure_dpi_awareness()` puts the process into
per-monitor-v2 mode once and never downgrades a level the host already set.

```python
from openeyes import ensure_dpi_awareness, click_element, find_elements

ensure_dpi_awareness()                       # once, at process start
hits = find_elements(elements, automation_id="KeepFreeAliveButton")
```

- **Prefer `automation_id`** over the visible name: a countdown label
  ("0:18 left - click to reset") changes every second, the control id does not.
- `click_element(hwnd, automation_id=..., dry_run=True)` resolves a live
  pywinauto wrapper so the **Invoke** pattern can be used (UIA-only).
- `move_to` moves in steps and settles before the press; a single
  `SetCursorPos` followed by a click can be delivered at the *previous*
  cursor position because the window has not processed `WM_MOUSEMOVE` yet.
- `click_xy` holds the button for `press_ms` - XAML/UWP controls can ignore a
  zero-duration click.

## Lightpanda backend (CDP, low RAM, unattended)

Lightpanda is an AI-native headless browser written in Zig with a minimal
memory footprint (about 123 MB peak for 100 pages, vs about 2 GB for Chrome
headless). It speaks Chrome DevTools Protocol on port 9222 and is binary-
compatible with Puppeteer / Playwright clients.

```bash
# WSL2 + Docker 29.x already installed on this host. The CLI wraps the run.
eyes browser launch --backend lightpanda --url https://example.com
```

MCP: `browser_launch(backend="lightpanda", url="https://example.com")`.

### When Lightpanda is the right pick

- Bulk structured extraction across many URLs / sessions.
- Concurrent crawls where Chrome headless would exhaust RAM.
- Unattended batch jobs that do not need human-in-the-loop.
- Cookies / profile can be discarded at the end of each session.

### When Lightpanda is NOT the right pick

- Login wall / 2FA / persistent cookies across launches. Each Lightpanda
  session has its own cookies and there is no profile seed.
- Anything that needs a screenshot. Lightpanda has no graphical rendering
  backend; `eyes browser shot` returns an empty PNG on this backend.
- Canvas-only / WebGL UIs (Lightpanda does not render pixels).

### Prerequisites (check before trusting a green run)

Lightpanda needs WSL2 with a **running Docker daemon inside the distro**:

```powershell
wsl -e bash -lc "docker version"        # the check
wsl -e bash -lc "sudo systemctl start docker"   # the fix
```

The daemon does not always come back after a WSL restart (`dockerd` exits 1,
"Start request repeated too quickly"). When it is down, `launch_lightpanda`
raises `LightpandaError` naming that cause - it does not hang.

### Hard-won facts

- Requires WSL2 + Docker on Windows. `wsl -e docker run -d --name lightpanda --rm -p 127.0.0.1:9222:9222 lightpanda/browser:nightly`
- WSL forwards `localhost:9222` to the Windows host, so the OpenEyes CDP client keeps using `127.0.0.1:9222`.
- Health probe: `http://127.0.0.1:9222/json/version` must respond before the launch returns.
- First run pulls lightpanda/browser:nightly (~150 MB); subsequent runs reuse the local image.

## Backend priority — structure first, pixels last

The 2026 computer-use consensus, now executable in openeyes:

```
uia  ->  cdp  ->  vision
```

Never reach for the pixel tier while a structured tier still answers.

```bash
# which tier answers for this window, and why did the earlier ones fall through?
eyes resolve --window <hwnd> --pretty

# turn OmniParser v2 detections into structured elements (no model, no network)
eyes vision --detections det.json --pretty
```

```python
from openeyes import resolve_elements

result = resolve_elements(hwnd=1234, port=9222)
print(result.used_backend)               # "uia" | "cdp" | "vision" | None
print([a.to_dict() for a in result.attempts])   # per-tier trail
```

Both commands are read-only. `eyes resolve` never actuates.

## When to use which backend
| Scenario | Backend | Why |
| --- | --- | --- |
| Any VPN / Edge native shell / VS Code / Feishu / Word / Excel | UIA | Fast, free |
| Real Chromium browser page (login wall, multi-step form, JS-driven UI) | CDP | Structured, no image cost |
| WebView2 inside a UWP / Edge legacy URL bar / canvas-only UI | CDP (browser) | UIA only sees the shell |
| Custom canvas game / image-only button | Vision (parsing-style) | Last resort; the screen is parsed into elements, not clicked blind |
| Bulk structured extraction, concurrent crawls, unattended batch jobs | CDP (`--backend lightpanda`) | ~123 MB RAM, 10x faster boot than Edge; no persistent profile, no screenshot |

## Tips

- Find hwnd: `eyes windows list --title-contains "飞书"` → grab `hwnd` field.
- Combine `--name-contains` with `--control-type` (e.g. `Button`) to disambiguate.
- Pass `--return-to-origin` after a click to leave the user's cursor where
  it was.
- After clicking a state-changing button (e.g. `Secure my connection`),
  re-run `eyes detect` (UIA) or `eyes browser scan` (CDP) to verify the
  new state — the source of truth is the live element tree, not pixels.
- UWP apps: title is on `ApplicationFrameWindow`; controls live inside a
  child `Windows.UI.Core.CoreWindow`. Pass `--restore` to call
  `ShowWindow(SW_RESTORE)` before enumerating (the tree is empty when
  minimized).
- CDP browser path defaults to `--port 9222`. Reuse a ``--profile-dir``
  across scripts to keep cookies alive (no re-login). Pass ``--no-seed`` to
  skip copying live profile slices into the temp dir.
- CDP coordinates are viewport-relative — independent of Edge window
  position. Same DOM element, same numbers, every time.

## Hard-won facts (this machine)

- Python 3.11.5, Pillow 11.2.1, pywinauto 0.6.9, pywin32, websocket-client 1.8.0.
- DPI: multi-monitor 3840x1200. Use window-relative coords; absolute coords
  break when the window moves.
- `pywinauto.rect.left/top/width/height` may be int property or callable —
  use `_call()` wrapper.
- `node.children` in UIA backend is a method — `node.children()`.
- Chromium ~v111 requires ``--remote-allow-origins=*`` on the debug-
  enabled browser or WebSocket handshakes get 403. OpenEyes ``launch_edge``
  passes this flag by default.
- Chromium's ``webSocketDebuggerUrl`` often strips the port
  (``ws://127.0.0.1/...``); ``connect()`` stitches ``:9222`` back.
- Edge ``User Data`` is locked when live Edge runs; ``launch_edge`` seeds
  the temp userDataDir from just the session slices (Preferences /
  Cookies / Local Storage / Network / IndexedDB).
- 先判断是否有 API/CLI/DOM 可走：能走就不要开浏览器自动化。

## Python API

```python
# Native (UIA)
from openeyes import find_window, detect_elements, find_elements, click_by_selector
wins = find_window(title_contains="飞书")
elems = detect_elements(wins[0].hwnd, restore=True)
msg = find_elements(elems, name_contains="消息")[0]
click_by_selector(wins[0].hwnd, name_contains="消息", dry_run=False)

# Browser (CDP)
from openeyes.backends import cdp
from openeyes.core.hints import assign_hints

info = cdp.launch_edge(url="https://example.com", seed=False)
conn = cdp.connect(port=9222, url_contains="example.com")
elems = cdp.scan_dom(conn)
assign_hints(elems)
go = next(e for e in elems if e.name == "GO")
cdp.click_center(conn, go)
```

## Decision layer: local System One instead of a hosted key

`openeyes.decision` can score UI candidates with a real judge over the
System One wire. The hosted endpoint needs `TYPESAFE_API_KEY`; a **local
XERJ node speaks the same wire** on its native REST port, so the layer runs
without a hosted key:

```powershell
$env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
$env:XERJ_API_KEY = (Get-Content E:\AI-Portable\xerj-data\admin.key -Raw).Trim()
python examples\systemone-local-acceptance.py   # prints one JSON line, exit 0
```

A local history vote answers with per-option **support scores** (the
winner's share of the labelled neighbours, losers at 0.0), not a probability
distribution - its confidence scale is its own, so thresholds tuned for the
hosted judge do not transfer unchanged. See the `xerj` skill.

## Quality gate

Before a push:

```powershell
pwsh -NoProfile -File examples\quality-gate.ps1
```

Lint (ruff), the encoding sweep (no UTF-8 BOM), the MCP stdio contract
(15 tools) and the test suite - the same four steps CI runs on every push to
`main`. A skipped test is not a passing test.

## Repository stewardship (codex is authorized)

As of 2026-08-25 the user has granted **full delegation** on this repo:

### Authority
- `git commit`, `git push` to `origin/main` (SSH:443 fallback per host `MEMORY.md` §7).
- Open pull requests against `yangpeng366/openeyes` from a topic branch (e.g. `fix/<short-slug>`).
- Fix typos, doc bugs, small refactors, contract drift between `docs/capability-contract.md` and the MCP / CLI surface — all in scope without per-change approval.
- Re-run the local test suite (`pytest tests/`) before pushing; skip only when the change is docs-only.

### Git identity (hard rule)
- All commits must be authored as `yangpeng <yangpeng@sobey.com>`. The repo-local config already sets this; verify with `git config --get user.name` / `user.email` before any commit.

### Red lines
- **No force push** to `main`. Rebase / amend only on the same local branch before the push lands.
- **No push of `.codex/`** — local round artifacts; keep on disk, do not `git add`. Add `.codex/` to `.gitignore` as a follow-up commit if it is not already ignored.
- **No destructive public-surface changes** in a single push. Removing or renaming an MCP tool, a CLI command, or breaking the documented MCP tool contract (15 tools as of v0.2.0) requires a feature branch + PR with rationale in the body.
- **No secrets** in commits. The repo has no secrets today; if any are ever needed, use env vars and document in the PR.

### Push workaround (Windows host)
- Direct `https://api.github.com` push times out on this host (see host `MEMORY.md` §7). Use the SSH-over-443 remote:
  `git remote set-url origin ssh://git@ssh.github.com:443/yangpeng366/openeyes.git`
- If SSH:443 also fails, fall back to Python `urllib.request` (TLS negotiation differs from curl).

### What the user still wants to be told about
- Any push that lands on `main` (one-line summary in the daily session brief).
- Any new PR opened (link + one-paragraph rationale).
- Any blocked push (network / 2FA / repo permission) — escalate before retrying.
