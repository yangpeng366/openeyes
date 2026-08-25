---
name: openeyes
description: Use OpenEyes (eyes CLI / openeyes Python package / openeyes-mcp server) to see and operate any desktop UI on Windows/macOS/Linux. Includes UIA-backed native apps (Win32/UWP), CDP-backed Chromium browser pages (Edge/Chrome via DevTools Protocol — no screenshot, no vision model, structured DOM), Vimium-style letter hints, Vimium-style 3x3 grid, focus/click/type/hotkey/drag/screenshot. Use when Codex needs to (a) drive a native app without an API or DOM, (b) drive a real browser session with login cookies / 2FA, or (c) read or write page elements (text input, button click) without spending image-budget. Browser path uses raw websocket-client on Chrome DevTools Protocol; Vision backends (OmniParser / Florence-2) remain as Phase-3 fallback for canvas / image-only UIs.
---

# OpenEyes Skill

> Repo: <https://github.com/yangpeng366/openeyes> · Live at github.com/yangpeng366/openeyes

OpenEyes is the AI computer-use primitive layer on this machine. It replaces
hardcoded mouse coordinates with **structured detection**: enumerate
interactive elements, find the one matching the user's intent, click its
center. LLM never has to read pixels.

## Three backends, one schema

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
3. **Vision** — Phase 3 fallback for canvas / image-only UIs (OmniParser,
   Florence-2). Not yet wired.

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

`eyes-mcp` exposes 13 primitives as MCP tools:

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

Start: `eyes-mcp` (stdio transport).

MCP side-effecting tools default to `dry_run=true`; explicitly pass
`dry_run=false` before writing files, launching a browser, sending keys, or
typing text. `browser_type` may scan the DOM in dry-run mode when a selector
is supplied, but it never focuses, types, or presses Enter until execution is
explicitly enabled.

## When to use which backend

| Scenario | Backend | Why |
| --- | --- | --- |
| Any VPN / Edge native shell / VS Code / Feishu / Word / Excel | UIA | Fast, free |
| Real Chromium browser page (login wall, multi-step form, JS-driven UI) | CDP | Structured, no image cost |
| WebView2 inside a UWP / Edge legacy URL bar / canvas-only UI | CDP (browser) | UIA only sees the shell |
| Custom canvas game / image-only button | Vision (Phase 3) | Last resort |

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
- **No destructive public-surface changes** in a single push. Removing or renaming an MCP tool, a CLI command, or breaking the documented 13-tool contract requires a feature branch + PR with rationale in the body.
- **No secrets** in commits. The repo has no secrets today; if any are ever needed, use env vars and document in the PR.

### Push workaround (Windows host)
- Direct `https://api.github.com` push times out on this host (see host `MEMORY.md` §7). Use the SSH-over-443 remote:
  `git remote set-url origin ssh://git@ssh.github.com:443/yangpeng366/openeyes.git`
- If SSH:443 also fails, fall back to Python `urllib.request` (TLS negotiation differs from curl).

### What the user still wants to be told about
- Any push that lands on `main` (one-line summary in the daily session brief).
- Any new PR opened (link + one-paragraph rationale).
- Any blocked push (network / 2FA / repo permission) — escalate before retrying.
