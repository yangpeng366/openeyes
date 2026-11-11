# OpenEyes Examples

Runnable demonstrations. Default behavior is dry-run unless `--go` is passed.

## WebView2 desktop apps

```powershell
# is a Tauri / Wails app exposing its DOM over DevTools?
python -m openeyes.cli.main webview describe --port 9333

# start the shell so that it does (close it first)
python -m openeyes.cli.main webview launch --exe "C:\Program Files\CCX\CCX Desktop\ccx-desktop.exe" --port 9333
```

A WebView2 app looks like a few empty panes to UIA - the tree stops at
`BrowserRootView`. The DOM is only reachable over the DevTools port, and
`webview describe` says so (with the exact way to open it) when the port
is closed.

## templates/ - three supervision templates

Keepalive, login and scheduled work are the same loop: read the state,
act when it is wrong, prove the act took effect, and schedule the next
round **from the state** rather than from a fixed timer. All three are
built on `openeyes.core.supervise`.

```powershell
# keepalive: renew while there is still margin left
python examples\templates\keepalive.py --title-contains "Any VPN" \\
    --automation-id KeepFreeAliveButton --trigger 10 --rounds 5

# login: prove the sign-in actually happened
python examples\templates\login.py --title-contains "Some App" \\
    --login-automation-id LoginButton --signed-in-automation-id UserMenu

# scheduled task: the command's own result is the proof
python examples\templates\scheduled_task.py --command "echo done" --every 300
```

Each template is dry-run by default; pass `--go` to act.

## measure-interference.py

Measure what a long-running loop costs and how often it lies: false
positives (successes that did not happen) and interference (cursor moves,
focus theft).

```powershell
# the built-in menu scenario: does the menu really open?
python examples\measure-interference.py --hwnd 12345 \\
    --name-contains "View" --menu-probe --rounds 4 --method invoke
```

Measured on this machine (2026-10-27, 4 rounds each): Invoke 4/4
effective, 0% false positives, 0 cursor moves; the pixel path 0/4
effective with 100% false positives - the click was delivered but the
menu never opened, because the window was not in the foreground.

## mouse-cluster-demo.py / interference-report.py

Two agents, two cursors, one desktop - and the receipt that shows nobody
was disturbed. The demo draws a cursor per agent (colour derived from the
identity), records every drawn frame in the action ledger under the agent
that drew it, and exits non-zero when the user's foreground window
changed. No entry drives the pointer: `physical_input` is false on every
one.

```powershell
# CI form: no window is created, but the ledger and the verdict are real
python examples\mouse-cluster-demo.py --dry-run --seconds 1

# a real run: two cursors, five seconds
python examples\mouse-cluster-demo.py --seconds 5
```

The ledger it writes is what the report reads:

```powershell
python examples\interference-report.py --ledger examples\out\mouse-cluster-ledger.jsonl
python examples\interference-report.py --ledger examples\out\mouse-cluster-ledger.jsonl --json
```

The report recounts `physical_input`, `focus_stolen`, `refused` and
`verified` entry by entry and compares them with the summary the run
itself wrote: exit 1 means the claim and the entries disagree (the
difference is printed), exit 2 means the file cannot be read as a ledger.
Measured here (2026-11-10, 2 s at 20 fps): 245 and 246 pixels of the two
agent colours on screen at once, foreground window unchanged, 82 ledger
entries with `physical_input=0`.

## App lifecycle

```powershell
# is the app up, or did it close its own window?
python -m openeyes.cli.main app state --title-contains "Any VPN" --process-name "Any VPN"

# revive it (dry-run by default; --go to act)
python -m openeyes.cli.main app ensure --title-contains "Any VPN" \
    --aumid "AnywaySoftInc.AnyVPN_0qkrc2qacwvfm!App" --go
```

`ensure_app` restores a hidden window without stealing the focus, relaunches
the app when the process is gone, and reports `ok: true` only when the app
was **observed** to be usable.

## anyvpn_keepalive.py

Keep Any VPN's free session alive by re-clicking the secure/reset button.

```powershell
# dry-run (default): enumerate window + target button, do not click
python examples\anyvpn_keepalive.py

# real click loop (default keepalive mode)
python examples\anyvpn_keepalive.py --go --interval 20

# first run when VPN is disconnected (one-time click Secure my connection)
python examples\anyvpn_keepalive.py --go --bootstrap --interval 20

# bounded test
python examples\anyvpn_keepalive.py --go --max-iter 5

# capture before/after screenshots per click for evidence
python examples\anyvpn_keepalive.py --go --bootstrap --screenshot-dir shots/anyvpn
```

### Desktop shortcuts (this machine)

3 .lnk files on the desktop, created 2026-08-11:

| Shortcut | Behavior |
|---|---|
| `OpenEyes - AnyVPN Keepalive.lnk` | Default: `--go --interval 20` keepalive loop. Use when VPN is already connected. |
| `OpenEyes - AnyVPN Bootstrap.lnk` | Passes `-Bootstrap`: one-time click of "Secure my connection" + keepalive. Use when VPN is disconnected. |
| `OpenEyes - AnyVPN DryRun.lnk` | Passes `-DryRun`: enumerates window + target, does NOT click. Safe preview. |

All three shortcuts call:
```
pwsh -NoProfile -ExecutionPolicy Bypass -File "E:\gitAll\openeyes\examples\launch-anyvpn-keepalive.ps1" [args]
```

### PowerShell launcher

`launch-anyvpn-keepalive.ps1` wraps the Python script with friendly flags:

```powershell
launch-anyvpn-keepalive.ps1                # default --go --interval 20
launch-anyvpn-keepalive.ps1 -Bootstrap     # click Secure my connection first
launch-anyvpn-keepalive.ps1 -DryRun        # safe preview
launch-anyvpn-keepalive.ps1 -Interval 30   # slower polling
launch-anyvpn-keepalive.ps1 -MaxIter 5     # bounded test
launch-anyvpn-keepalive.ps1 -Screenshot    # save before/after screenshots
launch-anyvpn-keepalive.ps1 -Help
```

## feishu_first_test.py


## remote_firefox_bidi.py - a Firefox on another machine, over SSH

Opens an SSH local forward to a remote Ubuntu Firefox (Remote Agent on 9222),
then lists tabs / scans / clicks / types through WebDriver BiDi. Click and type
are dry-run until `--go`.

```powershell
# list the remote tabs through the tunnel
python examples/remote_firefox_bidi.py --ssh-host yp-x240

# enumerate the interactive elements of the remote page
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --scan

# dry-run first, then act
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --click "Resend code" --go
python examples/remote_firefox_bidi.py --ssh-host yp-x240 --type 123456 --go
```

The remote side needs a Firefox started with the Remote Agent; the shipped
launcher does that with a visible window and a dedicated profile:

```bash
# on the Ubuntu box
bash examples/remote_firefox_launch.sh --port 9222 https://tenant.example/login
```

Runbook: `docs/remote-firefox-bidi.md`.

## dsh openeyes mount

### dsh-preflight.ps1

Read-only check that the OpenEyes MCP server, `dsh`, and the web-profile MCP client (version `0.1.1-rc.2`) are present before opening the dsh web UI.

```powershell
# basic preflight
pwsh -NoProfile -File examples\dsh-preflight.ps1

# also compose the web profile without starting the web UI
pwsh -NoProfile -File examples\dsh-preflight.ps1 -DumpConfig
```

Exit code `0` = `ready:true`. Exit code `2` = one or more prerequisites are missing; the JSON output lists them in `missing_prerequisites` and `next_action`.

### dsh-gate-readiness.py

Checks whether `http://127.0.0.1:3080/` returns HTTP 200 and prints the next
action for the Section 4 acceptance. It does not start dsh or issue any
browser tool call.

### maintenance-round-probes.ps1

Read-only orchestrator for the five deferred-acceptance probes that the patrol reruns on `2026-09-04T18:00:00+08:00`: git synchronisation, `pytest` suite, dsh preflight, the `9222`/`3080` listener + Edge window browser gate, and the repository-vs-installed skill SHA-256 check.

```powershell
# print the probe set only (default, safe)
pwsh -NoProfile -File examples\maintenance-round-probes.ps1 -Mode List

# run all five probes and write structured JSON evidence
pwsh -NoProfile -File examples\maintenance-round-probes.ps1 -Mode Run -ReportPath "$env:TEMP\openeyes-maintenance-round.json"
```

`List` exits `0` and writes no files. `Run` requires `-ReportPath` so probe output is captured to a UTF-8 (no BOM) JSON report; it does not perform the live `browser_click` acceptance, only the listener/window preconditions for it.

### open-acceptance-tabs.py

Opens the two disposable acceptance pages (`examples/acceptance-pages/target-a.html` and `decoy.html`) as new tabs in an already-running debug Edge (port 9222) via the CDP `/json/new` HTTP endpoint. Use this before the dsh-web `browser_click` acceptance so the `url_contains` probe has the right tabs.

```powershell
# dry-run (default): print fixture URLs and report port 9222 status
python examples\open-acceptance-tabs.py

# open both tabs in the live debug Edge
python examples\open-acceptance-tabs.py --go

# close any tab whose URL contains "acceptance-pages" (cleanup)
python examples\open-acceptance-tabs.py --close
```

`target-a.html` and `decoy.html` each contain a `Learn more` link so the acceptance verifies `url_contains` tab scoping rather than element absence.

### browser-click-acceptance.py

Runs the two-tab `browser_click` acceptance over direct MCP stdio while the dsh web host is unavailable. Open both fixture tabs first, run the probe, then close them:

```powershell
python examples\open-acceptance-tabs.py --go
python examples\browser-click-acceptance.py
python examples\open-acceptance-tabs.py --close
```

The probe passes only when the target tab remains a dry-run (`clicked:false`, `would_click:true`) and an unmatched `url_contains` fails closed with `no page target matched`.

### browser-scan-acceptance.py

Runs the two-tab `browser_scan` read-only acceptance over direct MCP stdio.
The script starts and cleans up its disposable HTTP fixture tabs. A unique
`target-a` scan resolves only the target page's `Secondary action`; the matching
decoy scan resolves `Learn more`; and an unmatched filter fails closed:

```powershell
python examples\browser-scan-acceptance.py --cdp-port 9222
```

### browser-type-acceptance.py

Runs the two-tab `browser_type` acceptance over direct MCP stdio. A matched
selector call stays dry-run, a selectorless call resolves and returns the exact
`target_url` without touching an element, and an unmatched selectorless filter
fails closed. The script starts and cleans up its disposable HTTP fixture tabs:

```powershell
python examples\browser-type-acceptance.py --cdp-port 9222
```

### browser-shot-acceptance.py

Runs the two-tab `browser_shot` acceptance over direct MCP stdio. The matched
dry-run must resolve `target-a` and report `captured:false` without creating a
PNG; the unmatched call must fail closed with `no page target matched
url_contains`; and then exactly one real capture lands in `--out`, verified by
reading the bytes back (PNG signature, IHDR geometry against the measured
viewport, closing IEND). The capture is staged as `browser-shot.png.part` and
only renamed after every check passed:

```powershell
python examples\browser-shot-acceptance.py --cdp-port 9222 --out examples\out\shot
```

### 真机双跑（browser-type / browser-shot）

Both write-tool probes now judge two independent surfaces and write a receipt
instead of printing `passed`:

| 判定 | page 面 | PowerShell 面 | 退出码 |
| --- | --- | --- | --- |
| PASS | 全部检查通过 | `pwsh` 退出 0 且 stdout 含 `200` | 0 |
| FAIL | 页面/元素不存在、CDP 报错、payload 违约 | 非零退出、超时、没有 `200` | 2 |
| SKIP | CDP 端口无人监听 | 找不到 `pwsh` | 3 |

总判定是 `FAIL > SKIP > PASS`：SKIP 不会被当成 PASS。同一页面连跑两轮时用
`--base-url` 固定 fixture 端口，让两轮的 URL 逐字节相同：

```powershell
# stable fixture server for both rounds
python -m http.server 18471 --bind 127.0.0.1 --directory examples

python examples\browser-type-acceptance.py --cdp-port 9222 --base-url http://127.0.0.1:18471 --out examples\out\evidence\type-round1
python examples\browser-type-acceptance.py --cdp-port 9222 --base-url http://127.0.0.1:18471 --out examples\out\evidence\type-round2
python examples\browser-shot-acceptance.py --cdp-port 9222 --base-url http://127.0.0.1:18471 --out examples\out\evidence\shot-round1
python examples\browser-shot-acceptance.py --cdp-port 9222 --base-url http://127.0.0.1:18471 --out examples\out\evidence\shot-round2
```

Measured on 2026-11-11 against visible Edge 152.0.4191.19: every round PASS
with identical `judgement_sha256`, and the two `browser-shot.png` files are
byte-identical (1640x1055, 33359 bytes, SHA-256 `61bb000a…aad62`).

Failure surfaces are provoked for real, never stubbed:

```powershell
# page: element missing / page missing / CDP error
python examples\browser-type-acceptance.py --inject-failure page --page-failure missing-element ...
python examples\browser-shot-acceptance.py --inject-failure page --page-failure missing-page ...
python examples\browser-shot-acceptance.py --inject-failure page --page-failure cdp-error ...

# PowerShell: a command that really fails
python examples\browser-type-acceptance.py --inject-failure powershell --ps-command "& 'C:\nope\missing-openeyes.exe'" ...
python examples\browser-shot-acceptance.py --inject-failure powershell --ps-command "exit 7" ...

# both
python examples\browser-type-acceptance.py --inject-failure both --page-failure missing-element --ps-command "& 'C:\nope\missing-openeyes.exe'" ...
```

`--out` behaviour: missing parents are created; an existing non-empty directory
is reused without deleting or overwriting anything; a path that cannot be a
directory fails with exit `2` and no receipt; an existing receipt is only
replaced with `--force`. An interrupted run (Ctrl+C/SIGBREAK, graceful kill)
exits `130` with `verdict=INTERRUPTED`, deletes the `.tmp` receipt and the PNG,
and leaves the `--out` directory empty. A hard kill cannot run cleanup: it can
leave a verified PNG, never a receipt.

### mcp-stdio-probe.py

Boots the repository-local MCP server directly, sends `initialize` + `initialized` notification + `tools/list`, and exits. Run this before the dsh web acceptance to isolate the Python/MCP mount from the dsh tool-dispatch layer:

```powershell
python examples\mcp-stdio-probe.py
```

Output: `ready:true`, `tool_count:13`. Exits `0`.

### dsh-fetch-stall-probe.py

Lightweight, read-only CDP probe that detects the post-initial-load `fetch()` stall observed in the dsh web client (the actual blocker on 2026-08-23). It only enables the `Runtime` and `Network` CDP domains and runs three small expressions in page context (`Promise.resolve(1)`, one `fetch('/api/host.describe')` with an `AbortController` timeout, and a read of the dsh session key in `localStorage`). It also performs one PowerShell HTTP request to the same endpoint for comparison.

```powershell
python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 5 --out "$env:TEMP\openeyes-dsh-fetch-stall.json"
```

Exit code `0` = no stall detected. Exit code `2` = page-context fetch stalled while the PowerShell fetch succeeded (the dsh fetch-stall symptom). Exit code `3` = CDP attach itself failed.

### dsh-session-diagnostic.py

Heavier read-only CDP listener that captures console calls, uncaught exceptions, requests or WebSocket frames containing `session.create` markers, matching response bodies, and changes to `dsh.sessions.current`. Run this only after the fetch-stall precheck confirms the blocker is still present.

```powershell
python examples\dsh-session-diagnostic.py --url-contains 127.0.0.1:3080 --seconds 180 --out "$env:TEMP\openeyes-dsh-session.jsonl"
```

Stop with `Ctrl+C` once the console evidence and `dsh_sessions_current` change are captured.

First showcase — drives the Feishu (Lark) client to verify the full pipeline.

```powershell
# dry-run (default): lists matches, does NOT click
python examples\feishu_first_test.py

# actual click
python examples\feishu_first_test.py --go

# target a specific element
python examples\feishu_first_test.py --target "通讯录" --go
```


## record_edge_demo.js

Reproducible headless Edge recording via the vendored `chrome-recorder-bridge`.
Launches Edge, loads a data-URL page with buttons and an input, performs 5 user
actions (click x3 + type), and writes one semantic Trajectory JSONL step per
action that Python `Trajectory.load()` and JS `parseJSONL()` can parse.

```powershell
# default output: examples/record-out/trajectory-edge-example.jsonl
node examples/record_edge_demo.js

# custom output path
node examples/record_edge_demo.js --out path/to/output.jsonl

# verify Python round-trip
python -c "from openeyes.record import Trajectory; t=Trajectory.load(r'examples/record-out/trajectory-edge-example.jsonl'); print(len(t.steps), 'steps loaded OK')"
```

Prerequisites: puppeteer-core, Microsoft Edge. No absolute D:/ paths required.

## verify_record_schema.py

Verifies UIA and Edge recordings share one Trajectory contract:

```powershell
python examples\verify_record_schema.py
python examples\verify_record_schema.py --uia examples\record-out\trajectory_real.jsonl --edge examples\record-out\trajectory-edge-real.jsonl --report examples\record-out\record-schema-report.json
```

It loads both files through `Trajectory.load`, canonicalizes them, reparses
with the vendored Node bridge, writes JSON evidence plus a machine-readable
summary, prints a one-line CLI result, and exits non-zero on a schema or
step-count mismatch. Action sequences are reported; add `--require-actions`
for recordings expected to be operationally identical.

## multi-monitor-smoke.py

Measures the coordinate maths that Windows hides: a window away from
(0, 0), a monitor at a negative coordinate, and a display at 125 % / 130 %
scaling. It enumerates every monitor with its DPI, then - only with
`--go` - opens its own window on the display under test, sends a real
click, and compares what the window received with the point that was
computed through `to_screen_xy`.

```powershell
# dry-run (default): the plan and every monitor's dpi; nothing is clicked
python examples\multi-monitor-smoke.py

# real windows and real clicks; the cursor is restored afterwards
python examples\multi-monitor-smoke.py --go

# machine-readable report, including expected/measured points
python examples\multi-monitor-smoke.py --go --json
```

A scenario the machine cannot exercise is `SKIP` with its reason and the
numbers behind it - a single-monitor laptop says
`SKIP negative_secondary_click: no secondary monitor with a negative origin
(displays: \\.\DISPLAY1 at (0,0))`, never a fabricated pass. Measured on
this machine (single 2240x1400 display at 130 %): both runnable scenarios
PASS with 0 px error, the negative-coordinate scenario SKIPs, and the
overall verdict stays `DRY-RUN` until `--go` is passed.
