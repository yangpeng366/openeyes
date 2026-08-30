# OpenEyes Examples

Runnable demonstrations. Default behavior is dry-run unless `--go` is passed.

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
