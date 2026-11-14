---
name: openeyes
description: "OpenEyes — computer-use / desktop automation layer for Windows, macOS, Linux (eyes CLI / openeyes / eyes-mcp). Use when an agent must drive a GUI: native Win32/UWP apps via UIA accessibility tree, Edge/Chrome via CDP structured DOM (no screenshot, no vision model), Vimium hints / 3x3 grid, Vision fallback (OmniParser v2), chain uia -> cdp -> vision. Focus, click, type, hotkey, drag, screenshot."
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
| `eyes focus --window H [--restore]` | Verified foreground switch; `--restore` un-minimizes; dry-run unless `--go` |
| `eyes anchor list/find/capture/click` | OpenCV template anchors (see below); click is dry-run unless `--go` |
| `eyes anchor probe/wait` | Scene probe: several anchors judged against ONE frame; read-only |
| `eyes scene --window H` | Read a window as text (UIA + OCR + OpenCV shapes); for agents that cannot see images |

## Anchor tier (OpenCV templates)

## Scene text tier (a window, read as text)

For an agent that cannot look at a picture - or an app whose accessibility tree is
just a window shell (Electron / canvas) - `eyes scene` reads the window and
answers with a list of elements, not an image:

```powershell
eyes scene --window 21174116                       # text digest (default)
eyes scene --window 21174116 --json               # full scene + text_digest
eyes scene --window 21174116 --source ocr         # skip the accessibility tree
```

```
窗口 Trae CN 1886x1183 来源=ocr 元素=42
[按钮] 新任务 @(77,167) 0.99
[文本] SOLO @(47,98) 0.99
```

- Degradation chain `uia -> ocr -> opencv`: the accessibility tree first (it carries
  real names), OCR for the text of a canvas app, OpenCV contours for the controls that
  carry no text at all.  OCR text that lands on a UIA element is dropped, so one place is
  never reported twice.
- The frame is taken with `PrintWindow` when the app allows it, so the window may sit
  behind other windows (verified on Trae CN); otherwise it falls back to a screen grab,
  which copies whatever is on top.  `capture.method` in the JSON says which path ran.
- Read-only: it never clicks, types or focuses.  The coordinates are window-relative;
  `eyes click --window H --x N --y N --go` is the acting counterpart.
- Shape detection stops at `panel / square / checkbox / icon / bar`.  It is evidence, not
  meaning: what a thing *is* stays the model's call.
- OCR needs the optional `rapidocr-onnxruntime` package (`pip install -e ".[ocr]"`); without
  it the scene degrades to the accessibility tree and says so in `notes`.

For apps with no usable UIA tree and no CDP page (Electron / VSCode forks such as
Trae CN), OpenEyes keeps a persistent template library and a guarded pixel click.
An anchor is structural evidence - "this patch of pixels appears here" - not
understanding; treat it as one tier, not as truth.

```powershell
eyes anchor list [--app trae] [--json]
eyes anchor find --window <hwnd> --app trae --name solo_badge --scale-product 1.3021 --check-frame --json
eyes anchor find --window <hwnd> --app trae --name solo_badge --scale-product 1.3021 --color-theme dark --json
eyes anchor find --image shot.png --app trae --name solo_badge --json   # offline read-only; no frame/safety gates
eyes anchor capture --window <hwnd> --app trae --name new_task_button --rect <measured-window-pixels> --scale-product 1.3021 --json
eyes anchor click --window <hwnd> --app trae --name send_button --check-frame --go   # dry-run without --go
```

### Scene probe (one frame, many verdicts)

```powershell
eyes anchor probe --window <hwnd> --app trae --names solo_badge,send_button --roi-pad 48 --json
eyes anchor wait  --window <hwnd> --app trae --names send_button --until present --timeout 900 --roi-pad 48 --json
eyes focus --window <hwnd> --restore --go --json   # un-minimize first, then verify and focus
```

- `probe` captures ONE frame and answers every name against it: `matched` /
  `ambiguous` / `missing` / `unreadable`, plus `state[name]` with `score`,
  `strength`, `center`, `screen_point` and `frame.sha256`. Use it for state
  decisions (running vs idle, SOLO badge, sidebar status); `find` is for one
  target and two finds are two frames, which can disagree while the UI moves.
- Never probe a minimized window: the frame is a placeholder strip, so the call
  returns `WINDOW_MINIMIZED`. Restore first with `eyes focus --window H --restore
  --go`, which re-reads the window identity after `ShowWindow(SW_RESTORE)`.
- `--roi-pad N` searches each template only near its stored rect +/- N px.
  Measured on Trae (1886x1183 @ 1.3021): 3 anchors whole-window ~2.9 s,
  `--roi-pad 48` ~0.12 s. The trade is positional: a control that moved farther
  than the pad reads as `missing`, and a byte-identical look-alike outside the ROI
  no longer causes a false `ambiguous`.
- `wait` polls `probe` until all names are present (`--until present`, default)
  or none are (`--until absent`). Timeout is a verdict (`ANCHOR_TIMEOUT`, with
  `last` holding the final frame), not an exception. Waiting never sends input,
  so it cannot resend a task by itself; pair it with the send receipt.
- Read-only by construction: neither verb clicks, focuses or writes the store.
- One widget, two states: capture the same control in each state (Trae: arrow = idle, filled square = running). The two anchors are mutually exclusive, so one frame settles the question without reading small labels.

- Store: `C:\Users\47037\.codex-home\anchors\<app>\` (`manifest.json` + `<name>.png`).
  Override with `--store` or `OPENEYES_ANCHOR_STORE`. PNGs are raw crops: no scaling,
  no thresholding, no alpha.
- Match: grayscale `TM_CCOEFF_NORMED`. The default sweep is 0.80-1.25 in 0.05 steps,
  multi-peak per scale, NMS `IoU > 0.3`. A known `scale_product` narrows the search
  to a +/-10% band around the expected ratio in 0.02 steps. A 150% DPI setup is near
  1.5 and lies outside the default sweep, so capture and find with the same explicit
  `--scale-product`. A missing or wrong value falls back to a coarse or wrong band.
- Confidence: `strong` >= 0.92, `weak` 0.80-0.92, `miss` < 0.80 (never clicks).
  A weak winner needs `--weak-support` and uniqueness; there is no extra
  corroboration step.
- Ambiguity: every hit >= 0.80 competes, whatever its tier. A rival within 0.06 of
  the best blocks the click (`ANCHOR_AMBIGUOUS`). `--threshold` / `--min-score` are an
  acceptance floor on the winner only: candidates are still searched down to
  `search_floor = min(threshold, 0.80)`, and a winner below the floor comes back as
  `ANCHOR_NOT_FOUND` with `score` / `hits` kept as evidence, plus
  `decision.candidate` and `below_acceptance: true`. Raising the threshold
  therefore cannot hide a rival.
- One control seen at several sweep scales is one hit: `nms()` keeps IoU
  suppression inside one scale step and merges across steps by centre
  distance (<= 0.5 w/h) plus size ratio <= 1.35, as connected components.
  Two copies that sit apart remain two hits and still read `ANCHOR_AMBIGUOUS`.
- Frame stability is live-only and opt-in with `--check-frame`: it compares the
  matched bbox across frames and reports `pixel_stable` and `template_stable`.
  Offline `--image` never performs this check: `--check-frame` with `--image` is a
  usage error, and the offline receipt carries
  `frame_stability: "not-applicable-offline"` and `actionable: false`: an offline match
  never justifies a click on its own.
  It proves "this crop did not change", not "this is the normal, non-hover state".
- Render context: entries carry `scale_product` (system scale x deviceScaleFactor x
  zoomLevel), `color_theme` and `theme`. Pass them when known; same-name entries are
  grouped by `scale_product`. `--color-theme` only orders candidates inside that group,
  it is not a filter. The OS app theme is not necessarily the application's own theme.
  Legacy manifests that carry only `dpi_scale` are still grouped: an entry's
  `scale_product` wins when the key exists, otherwise `dpi_scale` is used. An explicit
  `scale_product: null` means "captured without a known product" and is never grouped.
- Click gates, actual order: find resolves uniqueness/weak support first; click then
  requires foreground hwnd, valid window, not minimized, and `WindowFromPoint` root
  ownership. The last check is root-level only, so a same-window overlay, dropdown or
  tooltip can still swallow the click. This build has no hung-window probe; `IsWindow`
  means a valid handle, not an un-hung application.
- Failure codes: `ANCHOR_NOT_FOUND`, `ANCHOR_AMBIGUOUS`, `ANCHOR_WEAK`, `FRAME_UNSTABLE`,
  `FOREGROUND_MISMATCH`, `WINDOW_MINIMIZED`, `TARGET_OCCLUDED`, `INVALID_HWND`, `CLICK_FAILED`,
  plus `ANCHOR_TEMPLATE_MISSING`, `ANCHOR_STORE_NOT_FOUND`, `CAPTURE_FAILED`,
  `ANCHOR_VERIFY_FAILED` for setup problems. Exit 4 is a configuration/material
  failure, not necessarily an unreachable backend. Exit 5 includes safety refusals
  that never clicked. Parse `error_code`; do not blindly retry on 4 or 5.
- Capture discipline: park the pointer away from the control; capture one entry per
  scale and theme with a freshly measured rect; require three stable frames; then run a
  full-window offline find and keep the anchor only when `peaks=1`. Low-variance crops
  (grayscale std < 1.0) are rejected at match time and may surface as `CAPTURE_FAILED`.

### Anchor field rules

| Symptom | Cause | Rule |
| --- | --- | --- |
| 150% DPI always misses | default sweep tops out at 1.25 | Capture and find with an explicit `--scale-product 1.5`; require `scan_strategy.mode=scale-prior`, `used_group=true`, `fell_back=false`. |
| Old anchor dies after a theme change | grayscale correlation inverts; theme only reorders | Keep one template per theme; verify the matching screenshot scores >= 0.92 and the opposite theme misses. |
| A hover/pressed icon gets clicked | frame stability detects motion, not a static wrong state | Park the pointer away, wait out the animation, capture the normal state; use `--check-frame` for spinners and carets, not as proof of semantics. |
| A same-app overlay eats the click | `WindowFromPoint` checks only the root window | Cross-check `eyes detect` or `document.elementFromPoint` at the target point; close known overlays; keep the anchor as a fallback. |
| Wrong app or wrong window matched | app omitted, or a screen grab includes an overlapping window | Always pass `--app`; keep the target foreground and unobstructed; re-run find after any move or resize. |
| Capture passes but every find is ambiguous | capture proves stability, not uniqueness | Full-window offline find must return `peaks=1`; choose a text or uniquely textured region. |
| Existing anchor still reports `used_group=false` | the caller's `--scale-product` matched no entry, or the entry stores an explicit `null` product | Pass the product used at capture (legacy `dpi_scale` counts as the group) and require `used_group=true`. |
| Exit 4/5 is misdiagnosed | coarse exit codes mix causes | Read `error_code`: 3 means no target; 4 means store/template/capture setup; 5 includes safety refusals or a delivered action with no effect. |
| A minimized window reports every anchor `missing` | the capture is a placeholder strip, not the UI | `probe`/`wait` refuse with `WINDOW_MINIMIZED`; run `eyes focus --window H --restore --go` first. |

Consumer: `C:\Users\47037\.codex-home\lib\uix.py` (`find_template`) tries this store
first, then falls back to local templates plus OCR - see the `trae-client` skill.

## For AI agents

1. **Always pass `--json`** when you intend to parse the result.
2. **Check the exit code and `error_code`** - 0 is success; 3 means "no
   target matched"; 4 is a backend or configuration/material failure; 5
   is a safety refusal or an action that did not take effect. For anchors,
   trust `error_code` over the coarse exit class.
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
   Use **WebDriver BiDi** (`openeyes.backends.bidi`) for Firefox Remote Agent;
   both return the same structured `Element` schema.
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

### Browser (Firefox WebDriver BiDi)

Firefox Remote Agent is supported with the same scan/click/type verbs. It does
not need an API key; keep the Remote Agent bound to localhost.

```powershell
# Firefox must already be running with --remote-debugging-port=9222
# Direct connection
eyes browser tabs --protocol bidi

# SSH local forward 19222 -> remote localhost:9222
# Firefox checks the remote port in the HTTP Host header.
eyes browser scan --protocol bidi --port 19222 --host-header localhost:9222 --url-contains feishu.cn
eyes browser click --protocol bidi --port 19222 --host-header localhost:9222 --name-contains "验证码登录" --go
```

The `--host-header` option is only needed when the local socket port differs
from Firefox's Remote Agent port, such as an SSH forward. Firefox 159 may
accept the WebSocket handshake without echoing a subprotocol, so the backend
uses the BiDi wire directly and closes only the WebDriver session, not Firefox.

### Remote Firefox (Ubuntu over SSH)

A browser on another machine needs an SSH local forward, and Firefox's
Remote Agent checks the Host header of the forwarded request - so the
forward is part of the contract, not plumbing.

```powershell
# 1. on the remote box: Firefox with the Remote Agent, visible window
bash examples/remote_firefox_launch.sh --port 9222 https://tenant.example/login

# 2. on the control machine: tunnel + drive in one command
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --scan
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --click "Resend code" --go

# or own the forward and use the plain verbs
python -c "from openeyes.core.tunnel import SshTunnel"
eyes browser scan --protocol bidi --port 19222 --host-header localhost:9222 --pretty
```

What the forward teaches:

- `ExitOnForwardFailure=yes` - without it ssh survives a refused bind and the real cause scrolls past in the log.
- `--host-header localhost:9222` names the **remote** port, not the local one.
- Firefox 129+ has no CDP HTTP endpoints (`/json/version` -> 404); Firefox is BiDi only.
- A snap Firefox may only use a profile under `$HOME`; `firefox --version` prints harmless snapd mount warnings.
- The window stays visible; `DISPLAY=:0` is the logged-in seat, not the SSH session.
- Both ends bind `127.0.0.1`: a debug port is a privileged interface into a logged-in session.
- The transport is interchangeable - a Tailscale address, an office LAN address or an SSH jump host all work; only the far end of `ssh` matters.
- A login page may keep two steps in the DOM at once - `document.elementFromPoint(x, y)` says which one is on top before you click.

Runbook: `docs/remote-firefox-bidi.md`. Helper: `openeyes.core.tunnel.SshTunnel`.

Login walls, measured on a real Feishu login:

- A full-screen "Notice" overlay can swallow every click on the form below.
  `document.elementFromPoint(x, y)` at the target's coordinates says who would
  really get the click.
- Two steps of the form can be in the DOM at once; the inactive one intercepts
  clicks on the active one ("element click intercepted").
- The resend cooldown ("Resend in 39s") is the proof that a code went out - not
  the click, and not a patched fetch/XHR recorder (`sendBeacon` bypasses both).
- Firefox caps active BiDi sessions and a crashed probe never releases its
  session: restart the browser, or drive the page over Marionette (port 2828).

## MCP server

`eyes-mcp` exposes 23 primitives as MCP tools:

`anchor_probe` and `anchor_wait` mirror the read-only scene probe. Anchor
capture and `anchor click` stay CLI-only: capture needs a measured rect, and
click carries the foreground / occlusion safety gates.

**Lifecycle and verified action:**
- `app_ensure`        --title_contains / --process_name / --aumid / --dry-run=false
- `click_by_id`       --hwnd / --automation_id
- `act_and_verify`    --hwnd / --automation_id / --expect / --retries / --timeout
- `native_surfaces`   --app / --cli_names / --schemes / --exe_names
- `record_replay`     --file
- `anchor_probe`      --hwnd / --names / --roi_pad (read-only, one frame)
- `anchor_wait`       --hwnd / --names / --until / --timeout (read-only)
- `scene_describe`   --hwnd / --source / --max_items (read-only, returns text)

**Native (UIA):**
- `list_windows`
- `capture_window` (dry_run=true default; set false to write PNG)
- `detect_elements`
- `click` (dry_run=true default)
- `grid`
- `hotkey` (dry_run=true default; set false to send keys)
- `type_text` (dry_run=true default; set false to type)

**Browser (CDP or Firefox BiDi):**
- `browser_launch`  --url / --port / --no-seed / --dry-run=false
- `browser_tabs`    --protocol / --port / --host_header
- `browser_scan`    --protocol / --url_contains / --host_header
- `browser_click`   --protocol / --hint / --idx / --name_contains / --go / --host_header
- `browser_type`    --protocol / --text / --hint / --idx / --press_enter / --host_header
- `browser_shot`    --protocol / --out / --host_header

For Firefox through a forwarded port, pass `protocol=bidi` and
`host_header=localhost:9222` to these browser tools.

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
- The actuator layer is importable when no CLI verb covers the motion:
  `from openeyes import click_xy, move_to, drag, scroll, send_hotkey, type_text`.
  `drag(from_xy, to_xy, duration_ms)` presses, moves in steps and releases;
  `scroll(dx, dy, at_xy=None)` sends wheel deltas; `move_to` and `click_xy`
  carry the settle knobs above. To raise a native Win32 / UWP window without
  clicking it, call `openeyes.actuators.win32.focus_window(hwnd)` - it falls
  back to a title-bar click when `set_focus()` is refused. These are raw
  actuators with no dry-run gate: resolve first, then act.

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
| Real Firefox browser page / Remote Agent | WebDriver BiDi | Structured, no API key; supports forwarded Host header |
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
- Firefox 129+ dropped CDP: ``/json/version`` answers 404. Firefox is BiDi
  only - the websocket lives at ``ws://host:port/session``.
- Firefox's Remote Agent checks the ``Host`` header: behind an SSH forward
  pass ``--host-header localhost:9222`` - the remote port, not the local one.
- A remote Ubuntu Firefox needs ``--remote-debugging-port`` at launch, plus
  ``--remote-allow-hosts localhost``; a snap Firefox may only use a profile
  under ``$HOME``.
- A login page can keep two steps in the DOM at once; ``document.elementFromPoint(x, y)``
  tells which one is on top before a click, and the field values are read back
  before a submit.
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
(20 tools) and the test suite - the same four steps CI runs on every push to
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
- **No destructive public-surface changes** in a single push. Removing or renaming an MCP tool, a CLI command, or breaking the documented MCP tool contract (20 tools as of v0.2.0) requires a feature branch + PR with rationale in the body.
- **No secrets** in commits. The repo has no secrets today; if any are ever needed, use env vars and document in the PR.

### Push workaround (Windows host)
- Direct `https://api.github.com` push times out on this host (see host `MEMORY.md` §7). Use the SSH-over-443 remote:
  `git remote set-url origin ssh://git@ssh.github.com:443/yangpeng366/openeyes.git`
- If SSH:443 also fails, fall back to Python `urllib.request` (TLS negotiation differs from curl).

### What the user still wants to be told about
- Any push that lands on `main` (one-line summary in the daily session brief).
- Any new PR opened (link + one-paragraph rationale).
- Any blocked push (network / 2FA / repo permission) — escalate before retrying.
