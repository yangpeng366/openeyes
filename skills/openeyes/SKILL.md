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

## Command table

Every verb answers the same way: `--json` gives machine-readable output
(before or after the subcommand), the default is a human view of the
same data, and a failure is a non-zero exit code plus a message that
names the cause.


Every verb answers the same way: `--json` gives machine-readable output
(before or after the subcommand), the default is a human view of the
same data, and a failure is a non-zero exit code plus a message that
names the cause.

| Command | What it does |
| --- | --- |
| `eyes windows list` | Enumerate visible top-level windows (`--title-contains`, `--class-name`, `--regex`) |
| `eyes detect --window H` | Enumerate interactive elements (`--automation-id`, `--name-contains`, `--control-type`, `--regex`, `--depth`) |
| `eyes status --window H` | Buttons whose name contains an action verb - the "what can I press" snapshot |
| `eyes click --window H` | Click by coordinates or selector; **dry-run unless `--go`** |
| `eyes grid --window H --row R --col C` | Vimium-style grid cell click; dry-run unless `--go` |
| `eyes hotkey --combo ctrl+a` | Send a key chord; dry-run unless `--go` |
| `eyes type --text "..."` | Type literal text; dry-run unless `--go` |
| `eyes capture --window H --out f.png` | Screenshot to a PNG |
| `eyes resolve --window H` | Walk `uia -> cdp -> vision` and report which tier answered |
| `eyes vision --detections d.json` | Parse screen-parsing detections into elements (no model) |
| `eyes browser launch/tabs/scan/click/type/shot` | Drive any Chromium browser over CDP |
| `eyes app state` / `eyes app ensure` | Is the app alive? Revive it (window gone vs process gone) |
| `eyes webview describe` / `launch` | Reach the DOM inside a Tauri / Wails / Electron app |
| `eyes native find <app>` | Find the application's own CLI / URL scheme / registered executable / Store AUMID |
| `eyes record replay <file>` | Inspect a recorded trajectory |

## For AI agents

1. **Always pass `--json`** when you intend to parse the result.
2. **Check the exit code** - 0 is success; 3 means "no target matched",
   4 means "the backend could not be reached", 5 means "the action did
   not take effect".
3. **Read the error message** - it names the cause and the parameters
   that were in play. It is written to be actionable on its own.
4. **Verify the output exists** after a write; a command that exits 0
   is not proof that the state changed.
5. **Prefer `automation_id` over a visible name.** A name may change
   every second; the id does not.
6. **Actions are dry-run by default.** Pass `--go` (or `dry_run=false` on
   MCP) only when the action is intended.

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

`eyes-mcp` exposes 18 primitives as MCP tools:

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

## Act, then prove it worked

A delivered click is not an effective click. Any VPN's keepalive button carries
a live countdown ("0:18 left - click to reset"), so "the label changed" proves
nothing - only the countdown jumping back up proves the session was renewed.
`openeyes.core.verify.act_until` makes that distinction reusable.

```python
from openeyes import act_until, click_element

def act():
    return click_element(hwnd, automation_id="KeepFreeAliveButton",
                         method="auto", dry_run=False)

def expect():
    countdown = read_countdown(hwnd)      # business state, not the label
    return countdown is not None and countdown > before

report = act_until(act, expect, retries=3, timeout=1.2, interval=0.2)
# -> {"ok": True, "attempts": 1, "result": {...}, "elapsed": 0.9, "error": None}
```

- `ok` is True only when the expected state was **observed**; a delivered
  action that changed nothing is a miss, never a success.
- `on_retry(attempt, result)` fires only when another attempt follows - use it
  to switch strategy (Invoke first, then a pixel click).
- `expect` must read **business state** (a counter that went back up, a
  control that disappeared, a field that now holds a value), never "the label
  differs".

Three rules that keep a long-running loop usable:

1. **State-driven cadence, not a fixed interval.** A 20s poll against a 20s
   expiry clicks exactly at the expiry. Read the state and renew while a
   margin is left.
2. **Coexist with the user.** `SW_SHOWNOACTIVATE` restores a minimised
   window's (empty) accessibility tree without taking the foreground, and the
   Invoke pattern needs no focus and never moves the user's cursor.
3. **Assert the control's current meaning before clicking it.** One control
   can read "Secure my connection" in one state and "Disconnect" in the next;
   clicking the wrong meaning kills a live session.

## App lifecycle: window gone is two different diseases

A session timeout makes an app close its own window. "The window is gone"
is not a diagnosis: the **process** may still be alive (the app only closed
its window) or gone entirely. The two need different remedies - restoring
a window that exists, versus relaunching one that does not.

```python
from openeyes import app_state, ensure_app

state = app_state(title_contains="Any VPN", process_name="Any VPN")
# -> running | window-gone | not-running | unknown, with evidence

result = ensure_app(title_contains="Any VPN",
                    process_name="Any VPN",
                    aumid="AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App",
                    timeout=30.0, dry_run=False)
result.ok        # True only when the app was OBSERVED usable
result.action    # "none" | "restore" | "launch"
```

- `restore` uses `SW_SHOWNOACTIVATE`, so a minimised window comes back with
  a populated accessibility tree **without** stealing the foreground.
- `launch` starts a Store app through `shell:AppsFolder\<AUMID>`.
- `ok` is True only when usability was **observed** - a delivered launch is
  not an effective launch, the same rule as `act_until`.
- `ready=` tightens the bar, e.g. `ready=lambda hwnd: bool(detect_elements(hwnd))`.

```bash
# read-only: is the app up?
eyes app state --title-contains "Any VPN" --process-name "Any VPN"

# revive it (dry-run by default; --go to act)
eyes app ensure --title-contains "Any VPN" \
    --aumid "AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App" --go
```

MCP: `app_ensure(title_contains, process_name, aumid, timeout, dry_run)`.

## Act, then prove it worked - over MCP

The Python surface has `act_until`. MCP hosts now get the same discipline
through two tools, so an agent cannot mistake a delivered click for an
effective one:

```json
{"tool": "click_by_id",
 "arguments": {"hwnd": 12345, "automation_id": "KeepFreeAliveButton"}}
```

`click_by_id` addresses a control by its **stable** id and prefers the UIA
Invoke pattern - no pixel maths, no cursor movement, no focus theft. The
visible label of a live control ticks on its own; the id does not.

```json
{"tool": "act_and_verify",
 "arguments": {
   "hwnd": 12345,
   "automation_id": "KeepFreeAliveButton",
   "expect": {"element_appears": {"automation_id": "ConnectButton"}},
   "retries": 3, "timeout": 5.0
 }}
```

`expect` reads the **live accessibility tree**, not the label it just
wrote. Three shapes are supported: `element_appears`,
`element_vanishes`, and `label_matches` (a regex against a control's
visible label). `ok` is true only when the expected state was **observed**
after the action; a click that changed nothing is a miss, and the report
carries the evidence (`observed`) so a miss can be explained.

## Measuring a loop instead of trusting it

A long-running loop that lives on someone's desktop has to be judged on
two numbers: how often it claims a success that did not happen, and how
often it disturbs the human.

```bash
python examples\measure-interference.py --hwnd 12345 \\
    --name-contains "View" --menu-probe --rounds 4 --method invoke
```

Measured on this machine (2026-10-27, Notepad menu, 4 rounds each):

| Path | Effective | False positives | Cursor moves |
| --- | --- | --- | --- |
| **Invoke** | 4/4 | **0%** | 0 |
| Pixel | 0/4 | **100%** | 3/4 |

The pixel row is the lesson: the click was *delivered* (the cursor landed
on the control) yet the menu never opened, because the window was not in
the foreground. A loop that reported "delivered" as "success" would have
reported four successes and opened nothing.

Interference is attributed to the **automation**, not to the desktop: the
user's own cursor keeps moving, and the focus may already sit on the
target. A cursor move counts only when the automation drove the pointer
(a pixel click that landed); a focus theft counts only when the
automation pulled the focus onto the target from somewhere else.

## Desktop apps whose UI is a web view

Tauri, Wails and Electron apps paint their UI in a WebView2 surface. To
UIA such an app looks like a few empty panes: the accessibility tree
stops at `BrowserRootView` and the DOM is invisible. Two consequences:

- **The tree must be walked deep.** A WebView2 DOM sits around level 20;
  the old default of 12 could not see the controls. The default is now 30,
  and `eyes detect --depth N` / MCP `detect_elements(depth=N)` are
  explicit.
- **For the DOM itself, use the DevTools port.** WebView2 reads
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` when it creates its browser
  process, so a shell started with that variable in its environment gets
  `--remote-debugging-port`:

```bash
# is a web view exposing its DOM?
eyes webview describe --port 9333

# start a shell so that it does (the shell must be closed first)
eyes webview launch --exe "C:\Program Files\CCX\CCX Desktop\ccx-desktop.exe" --port 9333
```

Then the ordinary CDP tools work against it: `eyes browser tabs --port
9333`, `eyes browser scan --port 9333`, `eyes browser click --port 9333`.

Caveat worth knowing: a shell that sets its own WebView2 browser
arguments (Wails does) overrides the environment variable, and the port
stays closed. `eyes webview describe` says so instead of leaving you to
guess.

### What a WebView2 app looks like in practice (Yuanbao, 2026-10-27)

- The **send button** has a stable id: `yuanbao-send-btn`. Its visible
  name is "发送" - usable, but a name is not an anchor.
- The **input area** is a custom div: no `Edit` control appears in the
  tree, only the placeholder text "发送消息". Typing into it needs the
  DOM (CDP), not UIA.
- `act_and_verify` against `yuanbao-send-btn` succeeded on the first
  attempt in ~2.0 s, with the Invoke pattern and without moving the
  cursor.

## Supervision: keepalive, login and scheduled work are one loop

Three jobs that look different share one shape: **read the state, act
when it is wrong, prove the act took effect, schedule the next round from
the state**. `openeyes.core.supervise` is that loop, once.

```python
from openeyes import supervise, Health

report = supervise(
    health=lambda: Health(ok=countdown > 10, due_in=countdown - 10),
    act=lambda: click_element(hwnd, automation_id="KeepFreeAliveButton",
                               method="invoke", dry_run=False),
    expect=lambda: countdown > before,     # business state, not the label
    max_rounds=5,
)
report.effective        # rounds where the state really changed
report.misses           # delivered but nothing happened
report.false_positive_rate
report.interference_rate
```

Three rules the loop enforces:

1. **The next round is scheduled from the state.** A fixed 20s poll
   against a 20s expiry clicks exactly at 0:00 - the instant the session
   dies. A state-driven supervisor renews while there is still margin.
2. **Delivered is not effective.** A round counts as a success only when
   the expected state was observed afterwards.
3. **Interference is attributed to the automation.** The user's own
   cursor keeps moving, so a cursor move counts only when the automation
   drove the pointer.

Templates for the three common shapes live in `examples/templates/`:

| Template | The state it reads | The proof |
| --- | --- | --- |
| `keepalive.py` | a countdown | the countdown went back up |
| `login.py` | a presence (the login form) | the form is gone |
| `scheduled_task.py` | the clock | the command's own result |

## Troubleshooting cards

| Symptom | Cause | Fix |
| --- | --- | --- |
| **Empty control tree** | The window is minimised, so UIA has nothing to report | `restore_window(hwnd)` (no focus steal) or `detect_elements(hwnd, restore=True)` |
| **Element not found by name** | The visible label changes (a live countdown, "0:18 left - click to reset") | Anchor on `automation_id` (`KeepFreeAliveButton`), never the label |
| **Click lands a few pixels off** | The process is not DPI-aware, so coords and pixels disagree | `ensure_dpi_awareness()` once at process start |
| **Click delivered, nothing happened** | "The action was sent" was mistaken for "the action took effect" | `act_until(act, expect)`; `expect` must read business state (a counter that went back up), not the label |
| **App vanished mid-run** | Session timeout closed its window | `ensure_app(...)` - it tells restore from relaunch, and proves the app came back |

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

## Before you click: does the app have its own interface?

The cheapest way to drive an application is the interface it already
offers. A command line is deterministic, needs no coordinates and costs
no image budget; so is a URL scheme that carries a command. Look here
*before* reaching for the window.

```bash
eyes native find "Any VPN"
eyes native find "7-Zip" --cli-name 7z
eyes native find "印象笔记" --scheme evernote
```

Four shapes are probed, each with the lookup it needs:

| Shape | Where it hides | Example |
| --- | --- | --- |
| `cli` | next to the GUI executable, often **not** on PATH | `7z.exe` beside `7zFM.exe` |
| `url-scheme` | `HKCU\...\Classes\<scheme>` | `evernote:`, `obsidian://`, `vscode://` |
| `app-path` | `HKLM\...\App Paths` | `7zFM.exe` |
| `msix` | the shell's own application list | `AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App` |

The report lists what it probed, so "nothing found" is never confused
with "nothing looked for". When the shell reports an application as an
opaque id, the report says so rather than dressing it up as a runnable
path - a fake path is worse than no path.

MCP: `native_surfaces(app, cli_names, schemes, exe_names)` (read-only).

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
$env:XERJ_API_KEY = (Get-Content D:\AI-Portable\xerj-data\admin.key -Raw).Trim()
python examples\systemone-local-acceptance.py   # prints one JSON line, exit 0
```

A local history vote answers with per-option **support scores** (the
winner's share of the labelled neighbours, losers at 0.0), not a probability
distribution - its confidence scale is its own, so thresholds tuned for the
hosted judge do not transfer unchanged. Three consequences are built into the
layer:

- the thresholds follow the scale (`JevScore.scale`): the hosted judge keeps
  `0.85` / `0.60`, the local judge uses a measured profile, per language
  family (Chinese default act at 0.5121 / review at 0.2845, English opt-in
  act at 0.4552 / review at 0.3414) - see
  [`docs/jev-local-judge.md`](docs/jev-local-judge.md);
- **a goal and its option must share a language**, and seeding defaults to
  the Chinese family only - a mixed index lets one language's neighbours
  compete with the other's; English is opt-in (`--lang en`);
- a local vote retrieves by the question text, so it is asked for the plain
  option names its history knows (`JevScorer.candidate_key_style`);
- a judge that cannot answer (no labelled neighbour, 422) yields no step
  rather than a guess.

The judge's history and the measurement that set the thresholds:

```powershell
python examples\seed-systemone-history.py --index judgements --recreate            # Chinese default
python examples\seed-systemone-history.py --index judgements --recreate --lang en  # English opt-in
python examples\systemone-calibration.py
```

See the `xerj` skill for the node itself.

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
- **No destructive public-surface changes** in a single push. Removing or renaming an MCP tool, a CLI command, or breaking the documented MCP tool contract (18 tools as of v0.2.0) requires a feature branch + PR with rationale in the body.
- **No secrets** in commits. The repo has no secrets today; if any are ever needed, use env vars and document in the PR.

### Push workaround (Windows host)
- Direct `https://api.github.com` push times out on this host (see host `MEMORY.md` §7). Use the SSH-over-443 remote:
  `git remote set-url origin ssh://git@ssh.github.com:443/yangpeng366/openeyes.git`
- If SSH:443 also fails, fall back to Python `urllib.request` (TLS negotiation differs from curl).

### What the user still wants to be told about
- Any push that lands on `main` (one-line summary in the daily session brief).
- Any new PR opened (link + one-paragraph rationale).
- Any blocked push (network / 2FA / repo permission) — escalate before retrying.
