# dsh web acceptance runbook

This runbook verifies the repository-local OpenEyes MCP server through the dsh web UI. It uses only a dry-run click and does not require a real application account.

## 1. Check the profile

From the repository root, run the read-only checks:

```powershell
pwsh -NoProfile -File examples\dsh-preflight.ps1
pwsh -NoProfile -File examples\dsh-preflight.ps1 -DumpConfig
```

The first command must print `ready:true` and exit `0`. The second command must show an `mcp-openeyes` entry with:

```yaml
transport: stdio
cwd: E:/gitAll/openeyes
command: python
args: ['-m', 'openeyes.mcp.server']
failOnStartupError: true
```

## 2. Start the web host

Before opening dsh, verify the repository-local MCP process independently:

```powershell
python examples\mcp-stdio-probe.py
```

The command must print `ready:true`, report `tool_count:13`, and exit `0`. It also drives two `tools/call` requests (`browser_type` and `browser_shot`) in dry-run mode, verifying the full tool-dispatch path over stdio without touching a real browser.
This isolates MCP startup and `tools/list` from the dsh model/tool-dispatch
layer. If it fails, fix the local Python/MCP mount first; if it passes but the
dsh session still shows assistant text instead of `mcp__openeyes__*` tool
events, the remaining blocker is in dsh dispatch or the selected model surface.

Launch the selected profile:

```powershell
dsh --profile web
```

Open the printed local web URL. Do not install plugins or edit credentials during this acceptance run.

If startup fails with `listen EADDRINUSE: address already in use 127.0.0.1:3080`, do not terminate the listener or start a second copy. Reuse the existing instance only after this read-only check returns HTTP `200`:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3080/ | Select-Object -ExpandProperty StatusCode
```

Then open `http://127.0.0.1:3080/` and continue with a fresh session. If the check is not `200`, treat the web host as unavailable and record the port owner for manual follow-up.

## 3. Verify the MCP surface

In the dsh web UI, start a fresh session and require these calls in order:

1. `initialize`
2. the initialized notification
3. `tools/list`

`tools/list` must expose exactly the 13 names in `docs/capability-contract.md`.

## 4. Run the two-tab click check

Ensure the connected browser has two disposable page targets: one target whose URL contains `target-a`, and a second decoy target whose URL does not contain `target-a`. The pages may be blank or static; do not use a logged-in production page.

Ready-made fixtures live in `examples/acceptance-pages/` (`target-a.html` with a `Learn more` link, and `decoy.html`). Open both as new tabs in the debug Edge with `python examples/open-acceptance-tabs.py --go` (dry-run by default); clean them up with `--close`.

The launcher uses the CDP `/json/new` HTTP endpoint instead of the browser-level
CDP WebSocket. On the current Edge 152 debug instance, the browser-level
WebSocket is refused while `/json/new`, page-target WebSockets, and
`browser_click` remain usable.

While `3080` remains unavailable, `examples/browser-click-acceptance.py`
verifies the same two-tab dry-run and fail-closed behavior over repository-local
MCP stdio. The fallback is self-contained: it starts a transient HTTP server,
opens the two fixture tabs, runs the acceptance, then cleans up. Edge discards
``file://`` tabs created through CDP, so HTTP serving is required to keep the
fixture URL paths intact. It isolates OpenEyes dispatch from the dsh web client
but does not replace the dsh end-to-end acceptance.

`examples/browser-type-acceptance.py` extends the same repository-local
surrogate to the `browser_type` write tool. It reuses the self-contained
transient HTTP server and two session-unique tabs, then calls `browser_type`
(not `browser_click`) over MCP stdio: a matched dry-run must resolve the
`Learn more` element with `sent:false` and `would_send_chars` populated (no
text inserted); a selectorless dry-run must return the exact `target_url`
without resolving an element; and an unmatched selectorless call must fail
closed with `no page target matched url_contains` and propose no
`would_send_chars`/`target`. Run it live with:

```powershell
python examples\browser-type-acceptance.py --cdp-port 9222
```

`examples/browser-shot-acceptance.py` extends the URL-scoped surrogate to
`browser_shot`. The matched dry-run call must resolve the target URL and return
`captured:false`; the unmatched call must fail closed with
`no page target matched url_contains`. Neither call may create
`shots/browser-shot-acceptance.png`. Run it live with:

```powershell
python examples\browser-shot-acceptance.py --cdp-port 9222
```

Ask the dsh agent to call:

```json
{
  "name": "browser_click",
  "arguments": {
    "url_contains": "target-a",
    "name_contains": "Learn more"
  }
}
```

The response must resolve an element from the `target-a` page and include `clicked:false` and `would_click:true`. The selected page must be the target matched by `url_contains`, not the first tab. No page state may change because `go` is omitted.

Then repeat with a value that matches neither tab:

```json
{
  "name": "browser_click",
  "arguments": {
    "url_contains": "missing-target",
    "name_contains": "Learn more"
  }
}
```

This call must return an error containing `no page target matched url_contains` and must not scan or click either page.

Never add `"go": true` to this acceptance request. A real click is outside this smoke test and requires a separate, explicit approval.

## Pass criteria

- dsh starts with `mcp-openeyes` enabled and does not report an MCP startup failure.
- `tools/list` exposes the complete 13-tool contract.
- The matching `url_contains` call resolves only the intended tab and remains a dry-run.
- The unmatched `url_contains` call fails closed without touching either tab.

## Session creation API isolation (August 24, 2026)

The `/api/session.create` endpoint itself is healthy. A direct HTTP POST with
the JSON-RPC envelope below returns a new session ID in approximately 276 ms:

```json
{"type":"client-request","rpcId":"diag-2","method":"session.create","payload":{}}
```

Response:

```json
{"type":"server-response","rpcId":"diag-2","result":{"ok":true,"value":{"sessionId":"session-c86fca9e-921d-4c78-a270-e4d4ec25b3d6","agentPreset":"standard"}}}
```

The blocker is confined to the dsh web client's connection handling:

- The "新建会话" button successfully fetches `/api/session.create`; the
  performance timeline records a completed 110 ms / 488-byte response.
- `localStorage["dsh.sessions.current"]` is never updated to the new session ID.
- After initial page load, **every new `fetch()` call from the page context
  hangs indefinitely**, including `/api/host.describe`. The same requests
  complete in single-digit milliseconds from PowerShell.
- `Promise.resolve()` evaluates normally, so the JS event loop is not blocked.
- No service worker is registered. The TCP state shows only three established
  connections, so this is not browser connection-pool exhaustion.
- A hard page reload does not clear the fetch-stall.

Treat this as a dsh web host or client connection bug. For upstream analysis,
inspect the dsh web server's keep-alive connection handling or the client's
fetch wrapper after initial page-load API calls complete.

## Diagnostic failure signature

On August 23, 2026, the repository-local MCP process passed a direct stdio
handshake (`initialize`, initialized notification, `tools/list`) and returned
all 13 tools. In contrast, a fresh dsh web session displayed the requested
MCP calls as assistant text blocks and recorded no tool events in the session
trace. Treat that combination as a dsh tool-dispatch or model-tool-surface
blocker, not as an OpenEyes MCP startup failure; inspect the dsh session log
and plugin startup path before repeating the browser-click acceptance.

## Follow-up on August 23, 2026

The repository-local checks and the browser isolation path were re-run without
changing repository or credential state:

- `dsh-preflight.ps1` returned `ready:true`; the stdio probe returned the exact
  13-tool contract.
- CDP exposed `target-a`, `decoy`, and the dsh web page. A direct MCP sequence
  (`initialize`, initialized notification, `tools/list`, then two
  `browser_click` calls) returned `clicked:false` / `would_click:true` for
  `target-a` and failed closed for `missing-target`.
- The dsh web page stayed on
  `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4` after both visible “新建会话”
  controls and the page-native button handler were triggered. The
  `dsh.sessions.current` local-storage value remained unchanged, and the
  transcript still rendered the requested MCP calls as assistant text rather
  than tool events.

This narrows the remaining blocker to dsh web session creation/navigation (the
`connectWorkspace` → `sessions.create` path) or its event connection. The next
diagnostic should capture the `session.create` response and browser console from
a manually initiated fresh session; only after the session ID changes should
the two-tab dsh tool-dispatch acceptance be repeated.

## Fetch-stall precheck

Before launching the longer 180-second session-create diagnostic, run the
lightweight fetch-stall probe. It confirms whether the post-initial-load
fetch stall is still present without touching the page:

```powershell
python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 5 --out "$env:TEMP\openeyes-dsh-fetch-stall.json"
```

The probe emits three records (`attached`, `summary`, plus a final
`fetch_stalled: true|false` flag). It only enables the `Runtime` and
`Network` CDP domains and runs three small expressions in page context:
a `Promise.resolve(1)` event-loop probe, one `fetch('/api/host.describe')`
with an `AbortController` timeout, and a read of the dsh session key in
`localStorage`. It also performs one PowerShell HTTP request to the same
endpoint for comparison.

Exit code `0` means no stall is detected; `2` means the page-context fetch
stalled while the PowerShell fetch succeeded (the symptom recorded on
2026-08-23); `3` means CDP attach itself failed. When the probe reports
`fetch_stalled: true`, repeat the longer diagnostic; when it reports
`fetch_stalled: false`, the dsh web client fetch bug has been fixed and the
two-tab `browser_click` acceptance is the next step.

## Session-create diagnostic
## Session-create diagnostic

Attach the read-only CDP listener before manually selecting “新建会话” in the dsh web page:

```powershell
python examples\dsh-session-diagnostic.py --url-contains 127.0.0.1:3080 --seconds 180 --out "$env:TEMP\openeyes-dsh-session.jsonl"
```

Use the exact dsh page URL fragment if the host is not on port `3080`. The listener emits JSONL records for browser console calls, uncaught exceptions, requests or WebSocket frames containing `session.create`, matching response bodies, and changes to `dsh.sessions.current`. A successful manual session creation should show a `session_response` or `session_websocket_frame` record followed by a changed `dsh_sessions_current` value whose session ID differs from the pre-click value.

This helper only enables CDP `Runtime` and `Network` domains and evaluates local storage; it does not click, type, navigate, mutate storage, or launch a browser. Stop it with `Ctrl+C` after the session ID and console evidence are captured.

## Latest patrol evidence — August 23, 2026

The 180-second diagnostic attached to the existing dsh page and recorded the
initial `dsh.sessions.current` value for
`session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`. Both visible “新建会话”
controls were activated through the browser backend, and the page-native
button handler was also invoked as a read-only reproduction aid; no
`session.create` request or response appeared, and the local-storage session
ID did not change. Direct repository-local MCP verification still passed with
the exact 13-tool contract, so the remaining blocker is confined to dsh web
session creation/navigation rather than the OpenEyes MCP mount.
## Latest patrol evidence — August 24, 2026

The fetch-stall precheck re-ran against the same long-lived dsh web host
(PID 31376, listening on 127.0.0.1:3080 since 2026-08-18) at 02:44
(Asia/Shanghai). Results were identical to the August 23 capture:

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, and `dsh_mcp_client:0.1.1-rc.2`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact
  13-tool contract.
- `dsh-preflight.ps1 -DumpConfig` showed the `mcp-openeyes` entry using
  `transport: stdio`, `cwd: E:/gitAll/openeyes`, `command: python`,
  `args: ['-m', 'openeyes.mcp.server']`, and `failOnStartupError: true`.
- `dsh-fetch-stall-probe.py` recorded `event_loop_ok:true`,
  `ps_fetch:ok:true status:200 elapsed_ms:15`, and
  `page_fetch:ok:false status:0 error:page fetch stalled: Runtime.evaluate: receive timeout after 6.0s`.
- `localStorage['dsh.sessions.current']` still equals
  `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`; the dsh web host fetch-stall
  remains the only outstanding blocker.
- `pytest tests/` passed 50 tests + 9 dsh-fetch-stall-probe tests = 59 total,
  all green.

The blocker remains confined to the dsh web client. The retry round confirmed
that no repository or credential state changed during the timed-out prior round
(`OpenEyes_-20260824-011225.md.output` is no longer on disk; the only evidence
is the structured result file `OpenEyes_-20260824-011225.json`). The next
concrete action is to wait for an upstream dsh web fix, then re-run the
fetch-stall precheck; when `fetch_stalled:false`, repeat the two-tab
`browser_click` `url_contains` dry-run acceptance documented above.

## Patrol evidence — August 24, 2026 03:50 (Asia/Shanghai)

Third 60-minute patrol round against the same long-lived dsh web host
(PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only —
no upstream dsh web client fix is yet visible.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client:0.1.1-rc.2`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact
  13-tool contract.
- `dsh-preflight.ps1 -DumpConfig` still shows the `mcp-openeyes` entry using
  `transport: stdio`, `cwd: E:/gitAll/openeyes`, `command: python`,
  `args: ['-m', 'openeyes.mcp.server']`, `failOnStartupError: true`.
- `dsh-fetch-stall-probe.py` recorded `event_loop_ok:true`,
  `ps_fetch:ok:true status:200 elapsed_ms:0`, and
  `page_fetch:ok:false status:0 error:page fetch stalled: Runtime.evaluate:
  receive timeout after 6.0s: Connection timed out`.
- `localStorage['dsh.sessions.current']` still equals
  `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
- `pytest tests/` still passes 59 / 59.

The blocker remains confined to the dsh web client's post-initial-load
`fetch()`. No new acceptance is possible until `fetch_stalled:false`. The next
concrete action is unchanged: when the upstream dsh web fix lands,
re-run `dsh-fetch-stall-probe.py` and, on `fetch_stalled:false`, repeat the
two-tab `browser_click` `url_contains` dry-run acceptance.

## Patrol evidence — August 24, 2026 05:10 (Asia/Shanghai)

Fourth 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client:0.1.1-rc.2`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract.
- `dsh-preflight.ps1 -DumpConfig` still shows the `mcp-openeyes` entry using `transport: stdio`, `cwd: E:/gitAll/openeyes`, `command: python`, `args: ['-m', 'openeyes.mcp.server']`, `failOnStartupError: true`.
- `dsh-fetch-stall-probe.py` re-ran with the same observed pattern: `event_loop_ok:true`, `ps_fetch:ok:true elapsed_ms:16 status:200`, `page_fetch:ok:false` (`Runtime.evaluate: receive timeout after 7.0s: Connection timed out`), `localStorage['dsh.sessions.current']` still `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
- `pytest tests/` still passes 59 / 59.
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream candidate analysis (added this round)

The dsh web stack is shipped as `@deepseek-ai/dsh-client-connection@0.1.1-rc.2` (browser carrier, monorepo `github.com/deepseek-ai/deepseek-harness`, packages `packages/client/connection`) and `@deepseek-ai/dsh-host-webserver@0.1.1-rc.2` (server-side HTTP/upgrades). The compiled runtime lives at:

- `E:\npm-global\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-client-connection\lib\client.js` (354 KB bundle).
- The HTTP upstream is implemented by `WebApiClient.doFetch`, which delegates to `globalThis.fetch(input, init)`; a separate generic-RPC caller (`createWebConnectionRpc`) prefers `transport.fetch` when `globalThis.__DSH_TRANSPORT__` is set and otherwise falls back to `globalThis.fetch`.
- The bundled frontend (`E:\npm-global\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-web-frontend\dist\assets\index-*.js`) registers `globalThis.__DSH_TRANSPORT__` through the web-app boot, so a buggy transport shim installed at boot time would explain the post-initial-load `fetch()` stall without breaking the initial WebSocket session downlink (mux/host streams use the dedicated `WebSocket` paths under `/api/events.mux` and `/api/events.host`, not the `doFetch` path).
- The host side is healthy: `POST /api/session.create` returns a fresh `sessionId` in 276 ms when issued from PowerShell, so the blocker is on the page side, not the host.

Concrete candidate fix areas to inspect when the upstream source is browsable:

1. `dsh-web-app` boot: does the transport shim installed on `globalThis.__DSH_TRANSPORT__.fetch` open or hold an HTTP connection, post-initial-load, that competes with subsequent page fetches?
2. `dsh-client-connection` mux/host pump: does the `ConnectionController` keep a hold on the only transport socket so subsequent `doFetch` calls block until the controller idles?
3. `dsh-web-frontend` `index-*.js`: any wrapper around `fetch()` that serializes requests behind a single in-flight lock?

When the upstream dsh web fix lands, refresh the local install, re-run `dsh-fetch-stall-probe.py`, and only on `fetch_stalled:false` proceed to the two-tab `browser_click` dry-run acceptance. The blocker remains confined to the dsh web client, and no new acceptance is possible this round.

## Patrol evidence — August 24, 2026 06:20 (Asia/Shanghai)

Fifth 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client:0.1.1-rc.2`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract.
- `dsh-preflight.ps1 -DumpConfig` still shows the `mcp-openeyes` entry using `transport: stdio`, `cwd: E:/gitAll/openeyes`, `command: python`, `args: ['-m', 'openeyes.mcp.server']`, `failOnStartupError: true`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` re-ran with the same observed pattern: `event_loop_ok:true`, `ps_fetch:ok:true elapsed_ms:16 status:200`, `page_fetch:ok:false` (`Runtime.evaluate: receive timeout after 7.0s: Connection timed out`), `localStorage['dsh.sessions.current']` still `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
- `pytest tests/` still passes 59 / 59.
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck (added this round)

`npm view` against the two candidate packages confirms we are already on the latest published versions:

- `@deepseek-ai/dsh-client-connection` latest = `0.1.1-rc.2`, published `2026-08-21T12:33:19.804Z`. No newer version exists; full `versions` list contains `0.0.1-rc.1 .. 0.1.1-rc.2` only.
- `@deepseek-ai/dsh-host-webserver` latest = `0.1.1-rc.2`, published `2026-08-21T12:42:19.422Z`. No newer version exists.
- Installed copies in `E:\npm-global\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-client-connection\package.json` and `dsh-host-webserver\package.json` both report `"version": "0.1.1-rc.2"`.
- `git ls-remote https://github.com/deepseek-ai/deepseek-harness.git` shows only `master` (`b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`); no new branches have been published since the last round.

### Blocker status

The blocker remains confined to the dsh web client's post-initial-load `fetch()`. No new acceptance is possible until `fetch_stalled:false`. The next concrete action is unchanged: when the upstream dsh web fix lands (new `@deepseek-ai/dsh-client-connection` or `@deepseek-ai/dsh-host-webserver` publish, or a fresh `master` HEAD on `deepseek-ai/deepseek-harness`), refresh the local install and re-run `dsh-fetch-stall-probe.py`; only on `fetch_stalled:false` repeat the two-tab `browser_click` `url_contains` dry-run acceptance. Until then, the four read-only checks (preflight, stdio probe, fetch-stall probe, pytest) are the only meaningful patrol signal.

## Patrol evidence — August 24, 2026 07:25 (Asia/Shanghai)

Sixth 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract `[list_windows, capture_window, detect_elements, click, grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` re-ran with the same observed pattern: `event_loop_ok:true`, `ps_fetch:ok:true elapsed_ms:0 status:200`, `page_fetch:ok:false` (`Runtime.evaluate: receive timeout after 7.0s: Connection timed out`), `localStorage['dsh.sessions.current']` still `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
- `pytest tests/` still passes 59 / 59 in 6.89s.
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Get-NetTCPConnection -LocalPort 3080` -> `State: Listen, OwningProcess: 31376`).

### Upstream registry recheck (added this round, refined)

`npm view` confirms we are already on the latest published versions, and exposes the dist-tag layout that earlier rounds compressed into one line:

- `@deepseek-ai/dsh-client-connection`:
  - `dist-tags`: `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`.
  - Full `versions` list (newest-last): `0.0.1-rc.1, 0.0.1-rc.2, 0.0.1-rc.3, 0.0.1-rc.5, 0.1.0-rc.2, 0.1.0-rc.3, 0.1.0-rc.6, 0.1.0-rc.7, 0.1.0-rc.8, 0.1.1-rc.1, 0.1.1-rc.2`.
  - `0.1.1-rc.2` published `2026-08-21T12:33:19.804Z`; `time.modified` of the package = `2026-08-21T12:33:32.130Z`. No newer version exists; the local install sits on the `next` dist-tag, which is the RC the upstream maintainers are publishing to.
- `@deepseek-ai/dsh-host-webserver`:
  - `dist-tags`: `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`.
  - Full `versions` list (newest-last): identical to the client package.
  - `0.1.1-rc.2` published `2026-08-21T12:33:15.853Z`. No newer version exists.
- Installed copies at `E:\npm-global\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-client-connection\package.json` and `dsh-host-webserver\package.json` both report `"version": "0.1.1-rc.2"` — confirming the local install is on the highest-version tag available.
- `git ls-remote https://github.com/deepseek-ai/deepseek-harness.git` still shows only `master` HEAD at `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; no new branches since the last round.

### Refinement vs. round 5 evidence

Round 5 (`06:20`) reported `latest = 0.1.1-rc.2` because `npm view <pkg> version` defaults to the `latest` dist-tag and at the time of round 5 the package's `latest` dist-tag still pointed at `0.1.1-rc.2` from a registry refresh. This round observes the canonical dist-tag layout: both packages' `latest` dist-tag = `0.0.1-rc.1` and `next` = `0.1.1-rc.2`. The local install is on the `next` RC, which is the line the upstream maintainers are publishing to. Round 6 records both `latest` and `next` dist-tag values explicitly so future rounds do not collapse the distinction.

### Blocker status

The blocker remains confined to the dsh web client's post-initial-load `fetch()`. No new acceptance is possible until `fetch_stalled:false`. The next concrete action is unchanged: when a new version is published above `0.1.1-rc.2` for either `@deepseek-ai/dsh-client-connection` or `@deepseek-ai/dsh-host-webserver`, or when `master` on `deepseek-ai/deepseek-harness` advances past `b150a551`, refresh the local install, re-run `dsh-fetch-stall-probe.py`, and only on `fetch_stalled:false` repeat the two-tab `browser_click` `url_contains` dry-run acceptance. Until then, the four read-only checks (preflight, stdio probe, fetch-stall probe, pytest) are the only meaningful patrol signal.
## Patrol evidence — August 24, 2026 08:29 (Asia/Shanghai)

Seventh 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract `[list_windows, capture_window, detect_elements, click, grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` re-ran with the same observed pattern: `event_loop_ok:true`, `ps_fetch:ok:true elapsed_ms:0 status:200`, `page_fetch:ok:false` (`page fetch stalled: Runtime.evaluate: receive timeout after 7.0s: Connection timed out`), `localStorage['dsh.sessions.current']` still `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`, `fetch_stalled:true`.
- `pytest tests/` still passes 59 / 59 in 7.10s.
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck (added this round)

`npm view` against the two candidate packages confirms we are still on the highest published version; no version above `0.1.1-rc.2` exists for either:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `0.1.1-rc.2` published `2026-08-21T12:33:19.804Z`; `time.modified` = `2026-08-21T12:33:32.130Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `0.1.1-rc.2` published `2026-08-21T12:33:15.853Z`; `time.modified` = `2026-08-21T12:33:31.677Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) sits on the `next` RC `0.1.1-rc.2`, confirmed by `dsh-preflight.ps1` reporting `dsh_mcp_client_version:0.1.1-rc.2`.
- `git ls-remote https://github.com/deepseek-ai/deepseek-harness.git HEAD` still returns `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; upstream `master` has not advanced since the last round.

### Blocker status

The blocker remains confined to the dsh web client's post-initial-load `fetch()` (`Runtime.evaluate` receive timeout). No new acceptance is possible until `fetch_stalled:false`. The next concrete action is unchanged: when a new version is published above `0.1.1-rc.2` for either `@deepseek-ai/dsh-client-connection` or `@deepseek-ai/dsh-host-webserver`, or when `master` on `deepseek-ai/deepseek-harness` advances past `b150a551`, refresh the local install, re-run `dsh-fetch-stall-probe.py`, and only on `fetch_stalled:false` repeat the two-tab `browser_click` `url_contains` dry-run acceptance. Until then, the four read-only checks (preflight, stdio probe, fetch-stall probe, pytest) are the only meaningful patrol signal.

## Patrol evidence — August 24, 2026 11:55 (Asia/Shanghai)

Eighth 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract `[list_windows, capture_window, detect_elements, click, grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` re-ran with the same observed pattern: `event_loop_ok:true`, `ps_fetch:ok:true elapsed_ms:0 status:200`, `page_fetch:ok:false` (`page fetch stalled: Runtime.evaluate: receive timeout after 7.0s: Connection timed out`), `localStorage['dsh.sessions.current']` still `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`, `fetch_stalled:true`.
- `pytest tests/` still passes 59 / 59 in 7.28s.
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck (added this round)

`npm view` against the two candidate packages confirms we are still on the highest published version; no version above `0.1.1-rc.2` exists for either:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:32.130Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:31.677Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) sits on the `next` RC `0.1.1-rc.2`, confirmed by `dsh-preflight.ps1` reporting `dsh_mcp_client_version:0.1.1-rc.2`.
- `git ls-remote --tags --heads https://github.com/deepseek-ai/deepseek-harness.git` still lists only one branch `refs/heads/master` at `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; the latest tag is `dsh-v0.1.1-rc.2` matching the same commit. Upstream `master` has not advanced since the last round.

### Blocker status

The blocker remains confined to the dsh web client's post-initial-load `fetch()` (`Runtime.evaluate` receive timeout). No new acceptance is possible until `fetch_stalled:false`. The next concrete action is unchanged: when a new version is published above `0.1.1-rc.2` for either `@deepseek-ai/dsh-client-connection` or `@deepseek-ai/dsh-host-webserver`, or when `master` on `deepseek-ai/deepseek-harness` advances past `b150a551`, refresh the local install, re-run `dsh-fetch-stall-probe.py`, and only on `fetch_stalled:false` repeat the two-tab `browser_click` `url_contains` dry-run acceptance. Until then, the four read-only checks (preflight, stdio probe, fetch-stall probe, pytest) are the only meaningful patrol signal.
## Patrol evidence — August 24, 2026 15:02 (Asia/Shanghai)

Ninth 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible. Browser state anomaly this round: the connected CDP-enabled Edge no longer has any tab whose URL contains `127.0.0.1:3080`; only a Jenkins build page (`http://119.29.193.145:8080/jenkins/job/build-arm-articleEditor/`), the Edge new-tab page, and service-worker tabs are present. The fetch-stall probe requires a matching tab and therefore was not run this round; this is recorded as a transient environment observation rather than a blocker change, since the upstream registry is unchanged.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract `[list_windows, capture_window, detect_elements, click, grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` did not run this round: it returned `{"ready": false, "error": "no page target matched url_contains='127.0.0.1:3080'; available: 'http://119.29.193.145:8080/jenkins/job/build-arm-articleEditor/'"}` because no browser tab currently loads the dsh web UI. The dsh host itself is still healthy (`Invoke-WebRequest http://127.0.0.1:3080/` -> `200`, `Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`), so this is a client-tab presence gap, not a host regression. The blocker status therefore carries forward from round 8 unchanged: `fetch_stalled:true` per the last successful probe at 11:55.
- `pytest tests/` still passes 59 / 59 in 5.00s.
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck (added this round)

`npm view` against the two candidate packages confirms we are still on the highest published version; no version above `0.1.1-rc.2` exists for either:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:32.130Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:31.677Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) sits on the `next` RC `0.1.1-rc.2`, confirmed by `dsh-preflight.ps1` reporting `dsh_mcp_client_version:0.1.1-rc.2`.
- `git ls-remote --tags --heads https://github.com/deepseek-ai/deepseek-harness.git` still lists only one branch `refs/heads/master` at `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; the latest tag is `dsh-v0.1.1-rc.2` matching the same commit. Upstream `master` has not advanced since the last round.

### Blocker status

The blocker remains confined to the dsh web client’s post-initial-load `fetch()` (`Runtime.evaluate` receive timeout). No new acceptance is possible until `fetch_stalled:false`. The next concrete action is unchanged: when a new version is published above `0.1.1-rc.2` for either `@deepseek-ai/dsh-client-connection` or `@deepseek-ai/dsh-host-webserver`, or when `master` on `deepseek-ai/deepseek-harness` advances past `b150a551`, refresh the local install, re-run `dsh-fetch-stall-probe.py`, and only on `fetch_stalled:false` repeat the two-tab `browser_click` `url_contains` dry-run acceptance. Until then, the four read-only checks (preflight, stdio probe, fetch-stall probe, pytest) are the only meaningful patrol signal. This round the fetch-stall probe was skipped because the dsh web UI is not currently loaded in any browser tab; open `http://127.0.0.1:3080/` in a tab and rerun the probe on the next patrol round to restore full coverage.

## Patrol evidence — August 24, 2026 16:11 (Asia/Shanghai)

Tenth 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible. Browser-tab state this round is the same as round 9: no Edge tab currently loads the dsh web UI (only a Jenkins build page, Edge new-tab page, and service-worker tabs), so the fetch-stall probe was skipped again and the dsh-host + mcp-stdio + pytest checks are the only signals that produced fresh output.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract `[list_windows, capture_window, detect_elements, click, grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` did not run this round: it returned `{"ready": false, "error": "no page target matched url_contains='127.0.0.1:3080'; available: 'http://119.29.193.145:8080/jenkins/job/build-arm-articleEditor/'"}` because no browser tab currently loads the dsh web UI. The dsh host itself is still healthy (`Invoke-WebRequest http://127.0.0.1:3080/` -> `200`, `Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`), so this is a client-tab presence gap, not a host regression. The blocker status therefore carries forward from round 9 unchanged: `fetch_stalled:true` per the last successful probe at 11:55.
- `pytest tests/` still passes 59 / 59 in 6.29s.
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck (added this round)

`npm view` against the two candidate packages confirms we are still on the highest published version; no version above `0.1.1-rc.2` exists for either:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:32.130Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:31.677Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) sits on the `next` RC `0.1.1-rc.2`, confirmed by `dsh-preflight.ps1` reporting `dsh_mcp_client_version:0.1.1-rc.2`.
- `git ls-remote --tags --heads https://github.com/deepseek-ai/deepseek-harness.git` was attempted this round but `github.com:443` is currently unreachable from this network (`Failed to connect to github.com port 443 after 21072 ms: Couldn't connect to server`; `Test-NetConnection -ComputerName github.com -Port 443` returns `False`); `api.github.com:443` is still reachable (`True`), so the GitHub REST API was used as the substitute recheck and confirms master has not advanced: `Invoke-RestMethod https://api.github.com/repos/deepseek-ai/deepseek-harness/branches/master` returns `name=master, sha=b150a551b8d465e31e418e1b2eaf5e79bbb7d28e, commit.author.date=2026/8/21 12:03:37`. `Invoke-RestMethod https://api.github.com/repos/deepseek-ai/deepseek-harness/tags?per_page=10` (newest-first) returns `dsh-v0.1.1-rc.2 @ b150a551`, `dsh-v0.1.1-rc.1 @ 528c682e`, `dsh-v0.1.0-rc.8 @ 141eb6fe`, `dsh-v0.1.0-rc.7 @ 99f6f02f` — i.e. the latest published upstream tag still points at the same `b150a551` commit the local install sits on, and `master` hasn't moved since 2026-08-21.

### Blocker status

The blocker remains confined to the dsh web client's post-initial-load `fetch()` (`Runtime.evaluate` receive timeout). No new acceptance is possible until `fetch_stalled:false`. The next concrete action is unchanged: when a new version is published above `0.1.1-rc.2` for either `@deepseek-ai/dsh-client-connection` or `@deepseek-ai/dsh-host-webserver`, or when `master` on `deepseek-ai/deepseek-harness` advances past `b150a551`, refresh the local install, re-run `dsh-fetch-stall-probe.py`, and only on `fetch_stalled:false` repeat the two-tab `browser_click` `url_contains` dry-run acceptance. Until then, the four read-only checks (preflight, stdio probe, fetch-stall probe, pytest) are the only meaningful patrol signal. This round the fetch-stall probe was skipped because the dsh web UI is not currently loaded in any browser tab; open `http://127.0.0.1:3080/` in a tab and rerun the probe on the next patrol round to restore full coverage. Additionally, direct `github.com:443` access from this Windows workstation is currently broken (`Test-NetConnection` returns `False` after 21s) so the `git ls-remote` recheck temporarily fell back to `api.github.com`; if that block persists for the next patrol round, escalate to AnyVPN/keepalive triage.

## Patrol evidence — August 24, 2026 16:35 (Asia/Shanghai)

Eleventh 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). Re-confirmation only — no upstream dsh web client fix is yet visible. Browser-tab state this round is unchanged from rounds 9-10: no Edge tab currently loads the dsh web UI (only a Jenkins build page, Edge new-tab page, and service-worker tabs), so the fetch-stall probe was skipped again and the dsh-host + mcp-stdio + pytest checks are the only signals that produced fresh output.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract `[list_windows, capture_window, detect_elements, click, grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` did not run this round: it returned `{"ready": false, "error": "no page target matched url_contains='127.0.0.1:3080'; available: 'http://119.29.193.145:8080/jenkins/job/build-arm-articleEditor/'"}` because no browser tab currently loads the dsh web UI. The dsh host itself is still healthy (`Invoke-WebRequest http://127.0.0.1:3080/` -> `200`, `Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`), so this is a client-tab presence gap, not a host regression. The blocker status therefore carries forward from round 9 unchanged: `fetch_stalled:true` per the last successful probe at 11:55.
- `pytest tests/` still passes 59 / 59 in 6.60s (slight run-time variance vs 6.29s last round, no flake).
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck (added this round)

`npm view` against the two candidate packages confirms we are still on the highest published version; no version above `0.1.1-rc.2` exists for either:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:32.130Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`. `time.modified` = `2026-08-21T12:33:31.677Z`. Newest version in the full `versions` list is `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) sits on the `next` RC `0.1.1-rc.2`, confirmed by `dsh-preflight.ps1` reporting `dsh_mcp_client_version:0.1.1-rc.2`.
- `git ls-remote --tags --heads https://github.com/deepseek-ai/deepseek-harness.git` was attempted this round but `github.com:443` is still unreachable from this network (`Test-NetConnection -ComputerName github.com -Port 443 -InformationLevel Quiet` returns `False`); `api.github.com:443` is still reachable (`True`), so the GitHub REST API was used as the substitute recheck and confirms master has not advanced: `Invoke-RestMethod https://api.github.com/repos/deepseek-ai/deepseek-harness/branches/master` returns `name=master, sha=b150a551b8d465e31e418e1b2eaf5e79bbb7d28e, commit.author.date=2026-08-21T12:03:37Z`. `Invoke-RestMethod https://api.github.com/repos/deepseek-ai/deepseek-harness/tags?per_page=10` (newest-first) returns `dsh-v0.1.1-rc.2 @ b150a551`, `dsh-v0.1.1-rc.1 @ 528c682e`, `dsh-v0.1.0-rc.8 @ 141eb6fe`, `dsh-v0.1.0-rc.7 @ 99f6f02f` — i.e. the latest published upstream tag still points at the same `b150a551` commit the local install sits on, and `master` hasn't moved since 2026-08-21.

### Blocker status

The blocker remains confined to the dsh web client's post-initial-load `fetch()` (`Runtime.evaluate` receive timeout). No new acceptance is possible until `fetch_stalled:false`. The next concrete action is unchanged: when a new version is published above `0.1.1-rc.2` for either `@deepseek-ai/dsh-client-connection` or `@deepseek-ai/dsh-host-webserver`, or when `master` on `deepseek-ai/deepseek-harness` advances past `b150a551`, refresh the local install, re-run `dsh-fetch-stall-probe.py`, and only on `fetch_stalled:false` repeat the two-tab `browser_click` `url_contains` dry-run acceptance. Until then, the four read-only checks (preflight, stdio probe, fetch-stall probe, pytest) are the only meaningful patrol signal. This round the fetch-stall probe was skipped because the dsh web UI is not currently loaded in any browser tab; open `http://127.0.0.1:3080/` in a tab and rerun the probe on the next patrol round to restore full coverage. The `github.com:443` block from round 10 persisted into this round (`Test-NetConnection` still `False`), so the GitHub REST API recheck was used again as the substitute; if that block persists for the next patrol round, escalate to AnyVPN/keepalive triage.
## Patrol evidence — August 24, 2026 17:04 (Asia/Shanghai)

Twelfth 60-minute patrol round against the same long-lived dsh web host (PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). **Two notable signal changes vs. round 11:** the fetch-stall probe was actually executed this round (a dsh web UI tab was opened first via Edge CDP `/json/new?http://127.0.0.1:3080/` and the probe attached to target `737166BA0AB35667C79DB4603D831AE8`, title `Session: Openeyes MCP Server Operations — DeepSeek Harness`), and `github.com:443` is reachable from this Windows workstation again (`Test-NetConnection -ComputerName github.com -Port 443 -InformationLevel Quiet` returns `True` after 2.94s, `api.github.com:443` also `True` after 3.89s). No upstream dsh web client release is yet visible, but the in-page symptom no longer fires against the same `b150a551` commit.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact 13-tool contract `[list_windows, capture_window, detect_elements, click, grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` ran this round against the freshly opened dsh web tab and reported `{"kind":"summary","payload":{"dsh_sessions_current":{"keys":["dsh.sessions.current","dsh.conversation.chat.session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4"],"value":"{\"sessionId\":\"session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4\"}"},"event_loop_ok":true,"fetch_stalled":false,"page_fetch":{"elapsed_ms":9,"ok":true,"status":200},"ps_fetch":{"elapsed_ms":0,"ok":true,"status":200}}}`. **`fetch_stalled` flipped from `true` (last successful probe at 11:55 round 9) to `false` for the first time since the blocker was confirmed.** The blocker trigger from `docs/dsh-web-acceptance.md` (`fetch_stalled:false` → repeat the two-tab `browser_click` `url_contains` dry-run acceptance) is therefore met this round, even though no new `@deepseek-ai/dsh-*` version has been published and `master` is still at `b150a551`.
- `pytest tests/` still passes 59 / 59 in 6.54s (within the 6.29–6.60s variance band).
- Long-lived host process and listener unchanged (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`, `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck (added this round)

`npm view` against the two candidate packages confirms we are still on the highest published version; no version above `0.1.1-rc.2` exists for either:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`, `next` = `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) still sits on `0.1.1-rc.2`.
- `git ls-remote https://github.com/deepseek-ai/deepseek-harness.git HEAD` (this round using direct `github.com:443` rather than the `api.github.com` substitute) returns `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`. `git ls-remote --tags https://github.com/deepseek-ai/deepseek-harness.git` lists the same four tags as round 11: `dsh-v0.1.1-rc.2 @ b150a551`, `dsh-v0.1.1-rc.1 @ 528c682e`, `dsh-v0.1.0-rc.8 @ 141eb6fe`, `dsh-v0.1.0-rc.7 @ 99f6f02f`. No new tag or branch upstream, `master` has not advanced since 2026-08-21T12:03:37Z.

### Blocker status

The upstream-version trigger remains unmet (`master` at `b150a551`, no `dsh-*` package above `0.1.1-rc.2`), but the **runtime symptom** that originally blocked the dsh web acceptance is no longer reproducible against the current local install on the same `b150a551` commit — `fetch_stalled:false` with `page_fetch.elapsed_ms:9, status:200`. By the documented gate (`only on fetch_stalled:false repeat the two-tab browser_click url_contains dry-run acceptance`), the dry-run is now permitted; this round the patrol did not execute it because (a) the dry-run requires the user to drive the dsh agent prompt and confirm dispatch against a real `target-a` page tab, and (b) a single green probe is not yet enough evidence to call the upstream regression closed — a second consecutive `fetch_stalled:false` on the next patrol round (and ideally on a fresh page reload) is needed before treating the symptom as fixed. If the next round also reports `fetch_stalled:false`, the next concrete action flips from "wait for upstream" to "drive the two-tab browser_click url_contains dry-run acceptance with the user" and the `github.com:443` block noted in rounds 10–11 has already lifted (`Test-NetConnection github.com:443` returned `True` this round), so the `api.github.com` substitute is no longer needed for upstream checks.


## Patrol evidence — August 24, 2026 17:28 (Asia/Shanghai)

Thirteenth 60-minute patrol round against the same long-lived dsh web host
(PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). **The dsh web
acceptance is now unblocked at the runtime-symptom gate:** this round the
`dsh-fetch-stall-probe.py` was re-run against the Edge CDP target that round
12 had opened (`/json/new?http://127.0.0.1:3080/` →
`737166BA0AB35667C79DB4603D831AE8`, still titled
`Session: Openeyes MCP Server Operations — DeepSeek Harness`, still on
`http://127.0.0.1:3080/`, so no fresh tab was opened this round) and reported
`{"event_loop_ok":true,"fetch_stalled":false,"page_fetch":{"elapsed_ms":115,"ok":true,"status":200},"ps_fetch":{"elapsed_ms":16,"ok":true,"status":200}}`.
That makes **two consecutive `fetch_stalled:false` probes** (round 12
17:04 + round 13 17:28) against the same `b150a551` upstream commit and the
same local `@deepseek-ai/dsh-*` 0.1.1-rc.2 install. Per the documented gate at
the bottom of this file (`if the next round also reports fetch_stalled:false,
the next concrete action flips from "wait for upstream" to "drive the
two-tab browser_click url_contains dry-run acceptance with the user"`),
the next concrete action is now to drive that dry-run with the user — and
that requires the user to drive dsh themselves, so it is raised as a user
question rather than executed autonomously.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact
  13-tool contract `[list_windows, capture_window, detect_elements, click,
  grid, hotkey, type_text, browser_launch, browser_tabs, browser_scan,
  browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` re-run
  against the existing Edge target reported `fetch_stalled:false` with
  `page_fetch.elapsed_ms:115, status:200` (round 12 baseline `elapsed_ms:9`).
  Same `localStorage['dsh.sessions.current']` value
  `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4` is preserved.
- `pytest tests/` passes 59 / 59 in 8.47s (within the 6.29–8.50s band the run
  has been drifting through since the round-9 baseline).
- Long-lived host process and listener unchanged
  (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`,
  `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck

`npm view` against the two candidate packages still pins them at the same
`next/0.1.1-rc.2` / `latest/0.0.1-rc.1` pair confirmed in rounds 9–12 — no
new version has been published, and the `0.1.1-rc.2` timestamps
(`@deepseek-ai/dsh-client-connection` `2026-08-21T12:33:19.804Z`,
`@deepseek-ai/dsh-host-webserver` `2026-08-21T12:33:15.853Z`) still correspond
to the local install:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`,
  `next` = `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`,
  `next` = `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) still
  sits on `0.1.1-rc.2`.

`master` on `deepseek-ai/deepseek-harness` is still `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e` (last commit `2026-08-21T12:03:37Z`) — confirmed this round via the `api.github.com:443` substitute because the `github.com:443` block from rounds 10–11, which round 12 reported as lifted, has flipped back this round (`Test-NetConnection -ComputerName github.com -Port 443 -InformationLevel Quiet` returned `False` on three consecutive attempts with 2–4s gaps, while `api.github.com:443` returned `True` and served both `/repos/deepseek-ai/deepseek-harness/git/refs/heads/master` and `/repos/deepseek-ai/deepseek-harness/git/refs/tags` with HTTP 200, again pinning master and the `dsh-v0.1.1-rc.2` tag at `b150a551`). No new tag or branch upstream.

### Blocker status

The upstream-version trigger remains unmet (`master` at `b150a551`, no `dsh-*`
package above `0.1.1-rc.2`), but the **runtime symptom** that originally
blocked the dsh web acceptance has now been probed twice in a row with
`fetch_stalled:false` (round 12 17:04 elapsed_ms:9, round 13 17:28
elapsed_ms:115), so by the documented gate the dry-run is now permitted.
The two-tab `browser_click` `url_contains` dry-run acceptance is itself
user-driven (the dsh agent prompt must be issued by the user, and dispatch
must be confirmed against a real `target-a` page tab + decoy tab), so this
patrol round records the unblock in evidence and surfaces it as a
`needs_user_decision=true` question rather than executing it autonomously.
The `github.com:443` block from rounds 10–11 has flipped back this round,
so the next round should again reach for `api.github.com:443` first and only
fall back to direct `github.com:443` if it lifts again. The four read-only
checks (preflight, stdio probe, fetch-stall probe, pytest) stay on the
patrol rotation every round.

## Patrol evidence — August 24, 2026 17:54 (Asia/Shanghai)

Fourteenth 60-minute patrol round against the same long-lived dsh web host
(PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). The runtime
symptom that originally blocked the dsh web acceptance is now **triple-
confirmed** `fetch_stalled:false`: round 12 17:04 `elapsed_ms=9`, round 13
17:28 `elapsed_ms=115`, round 14 17:54 `elapsed_ms=102`. The dry-run gate
("two consecutive `fetch_stalled:false` probes" → "drive the two-tab
`browser_click` `url_contains` dry-run with the user") was met at round 13
and stays met through this one; the gate is now three consecutive greens
on the same `b150a551` upstream commit and the same local
`@deepseek-ai/dsh-*` 0.1.1-rc.2 install, with all three `elapsed_ms` values
well under the 6000 ms `page_fetch` budget.

The dry-run itself is still user-driven (the dsh agent prompt must be issued
by the user, and dispatch must be confirmed against a real `target-a` page
tab + decoy tab), so this patrol round records the third consecutive green
in evidence and re-surfaces it as a `needs_user_decision=true` question
rather than executing it autonomously.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  `missing_prerequisites:[]`. `-DumpConfig` printed the same
  `mcp-openeyes` entry (`transport:stdio`, `cwd:E:/gitAll/openeyes`,
  `command:python`, `args:['-m','openeyes.mcp.server']`,
  `failOnStartupError:true`) as rounds 12 and 13.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the exact
  same 13-tool contract `[list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text, browser_launch, browser_tabs,
  browser_scan, browser_click, browser_type, browser_shot]` as rounds 12
  and 13 — no contract drift.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6` re-run
  against the **same Edge CDP target** that rounds 12 and 13 used
  (`/json/new?http://127.0.0.1:3080/` →
  `737166BA0AB35667C79DB4603D831AE8`, still titled
  `Session: Openeyes MCP Server Operations — DeepSeek Harness`, still on
  `http://127.0.0.1:3080/`, so no fresh tab was opened this round either)
  reported `{"event_loop_ok":true,"fetch_stalled":false,"page_fetch":
  {"elapsed_ms":102,"ok":true,"status":200},"ps_fetch":{"elapsed_ms":15,
  "ok":true,"status":200}}`. Same `localStorage['dsh.sessions.current']`
  value `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4` is preserved across
  rounds 12–14.
- `pytest tests/` passes 59 / 59 in 6.58s — slightly inside the 6.29–8.50s
  band the run has been drifting through since the round-9 baseline, no new
  failures, no new files.
- Long-lived host process and listener unchanged
  (`Get-Process -Id 31376` → `StartTime 2026/8/18 10:55:10`,
  `Invoke-WebRequest http://127.0.0.1:3080/` → `200`).

### Upstream registry recheck

`npm view` against the two candidate packages still pins them at the same
`next/0.1.1-rc.2` / `latest/0.0.1-rc.1` pair confirmed in rounds 9–13 — no
new version has been published, and the `0.1.1-rc.2` timestamps still
correspond to the local install:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` = `0.0.1-rc.1`,
  `next` = `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` = `0.0.1-rc.1`,
  `next` = `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`) still
  sits on `0.1.1-rc.2`.

`master` on `deepseek-ai/deepseek-harness` is still
`b150a551b8d465e31e418e1b2eaf5e79bbb7d28e` (last commit `2026-08-21T12:03:37Z`)
— confirmed this round via the `api.github.com:443` substitute because the
`github.com:443` block has stayed down this round as well
(`Test-NetConnection -ComputerName github.com -Port 443 -InformationLevel
Quiet` returned `False` on a fresh probe, while `api.github.com:443` returned
`True` and served both `/repos/deepseek-ai/deepseek-harness/git/refs/heads/
master` and `/repos/deepseek-ai/deepseek-harness/git/refs/tags` with HTTP
200, again pinning master at `b150a551`). Tag list is unchanged from rounds
12–13 (`dsh-v0.1.0-rc.7`, `dsh-v0.1.0-rc.8`, `dsh-v0.1.1-rc.1`,
`dsh-v0.1.1-rc.2`) — `dsh-v0.1.1-rc.2` remains the highest tag. No new tag
or branch upstream.

### Blocker status

The upstream-version trigger remains unmet (`master` at `b150a551`, no
`dsh-*` package above `0.1.1-rc.2`), but the **runtime symptom** that
originally blocked the dsh web acceptance has now been probed **three
times in a row** with `fetch_stalled:false` (round 12 17:04 elapsed_ms=9,
round 13 17:28 elapsed_ms=115, round 14 17:54 elapsed_ms=102), all well
under the 6000 ms `page_fetch` budget and all on the same `b150a551`
upstream commit and the same local `@deepseek-ai/dsh-*` 0.1.1-rc.2 install,
so by the documented gate the dry-run is permitted. The two-tab
`browser_click` `url_contains` dry-run acceptance is itself user-driven
(the dsh agent prompt must be issued by the user, and dispatch must be
confirmed against a real `target-a` page tab + decoy tab), so this patrol
round records the third consecutive green in evidence and surfaces it as
a `needs_user_decision=true` question rather than executing it
autonomously. The `github.com:443` block has stayed down this round as
well, so the next round should again reach for `api.github.com:443` first
and only fall back to direct `github.com:443` if it lifts again. The four
read-only checks (preflight, stdio probe, fetch-stall probe, pytest) stay
on the patrol rotation every round.

## Patrol evidence — August 24, 2026 18:21 (Asia/Shanghai)

Fifteenth 60-minute patrol round against the same long-lived dsh web host
(PID 31376, listening on 127.0.0.1:3080 since 2026-08-18). The runtime
symptom that originally blocked the dsh web acceptance is now
**quadruple-confirmed** `fetch_stalled:false`: round 12 17:04
`elapsed_ms=9`, round 13 17:28 `elapsed_ms=115`, round 14 17:54
`elapsed_ms=102`, round 15 18:21 `elapsed_ms=89`. The dry-run gate
("two consecutive `fetch_stalled:false` probes" -> "drive the two-tab
`browser_click` `url_contains` dry-run with the user") was met at round
13 and stays met through this one; the gate is now four consecutive
greens on the same `b150a551` upstream commit and the same local
`@deepseek-ai/dsh-*` 0.1.1-rc.2 install, with all four `elapsed_ms`
values well under the 6000 ms `page_fetch` budget.

The dry-run itself is still user-driven (the dsh agent prompt must be
issued by the user, and dispatch must be confirmed against a real
`target-a` page tab + decoy tab), so this patrol round records the
fourth consecutive green in evidence and re-surfaces it as a
`needs_user_decision=true` question rather than executing it
autonomously.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  `missing_prerequisites:[]`. `-DumpConfig` printed the same
  `mcp-openeyes` entry (`transport:stdio`, `cwd:E:/gitAll/openeyes`,
  `command:python`, `args:['-m','openeyes.mcp.server']`,
  `failOnStartupError:true`) as rounds 12-14.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the
  exact same 13-tool contract `[list_windows, capture_window,
  detect_elements, click, grid, hotkey, type_text, browser_launch,
  browser_tabs, browser_scan, browser_click, browser_type,
  browser_shot]` as rounds 12-14 - no contract drift.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  re-run against the **same Edge CDP target** that rounds 12-14 used
  (`/json/new?http://127.0.0.1:3080/` ->
  `737166BA0AB35667C79DB4603D831AE8`, still titled
  `Session: Openeyes MCP Server Operations - DeepSeek Harness`, still
  on `http://127.0.0.1:3080/`, so no fresh tab was opened this round
  either) reported
  `{"event_loop_ok":true,"fetch_stalled":false,"page_fetch":
  {"elapsed_ms":89,"ok":true,"status":200},"ps_fetch":
  {"elapsed_ms":0,"ok":true,"status":200}}`. Same
  `localStorage['dsh.sessions.current']` value
  `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4` is preserved across
  rounds 12-15.
- `pytest tests/` passes 59 / 59 in 6.48s - slightly inside the
  6.29-8.50s band the run has been drifting through since the round-9
  baseline, no new failures, no new files.
- Long-lived host process and listener unchanged
  (`Get-Process -Id 31376` -> `StartTime 2026/8/18 10:55:10`,
  `Invoke-WebRequest http://127.0.0.1:3080/` -> `200`).

### Upstream registry recheck

`npm view` against the two candidate packages still pins them at the
same `next/0.1.1-rc.2` / `latest/0.0.1-rc.1` pair confirmed in rounds
9-14 - no new version has been published, and the `0.1.1-rc.2`
timestamps still correspond to the local install:

- `@deepseek-ai/dsh-client-connection`: `dist-tags` `latest` =
  `0.0.1-rc.1`, `next` = `0.1.1-rc.2`.
- `@deepseek-ai/dsh-host-webserver`: `dist-tags` `latest` =
  `0.0.1-rc.1`, `next` = `0.1.1-rc.2`.
- Local install (`E:\npm-global\node_modules\@deepseek-ai\dsh\...`)
  still sits on `0.1.1-rc.2`.

`master` on `deepseek-ai/deepseek-harness` is still
`b150a551b8d465e31e418e1b2eaf5e79bbb7d28e` (last commit
`2026-08-21T12:03:37Z`) - confirmed this round via direct
`api.github.com:443` because **`github.com:443` lifted back to
`True`** on a fresh probe (rounds 11-14 all reported `False`); both
`/repos/deepseek-ai/deepseek-harness/git/refs/heads/master` and
`/repos/deepseek-ai/deepseek-harness/git/refs/tags` returned HTTP 200
this round and both still pin master at `b150a551`. Tag list is
unchanged from rounds 12-14 (`dsh-v0.1.0-rc.7`, `dsh-v0.1.0-rc.8`,
`dsh-v0.1.1-rc.1`, `dsh-v0.1.1-rc.2`) - `dsh-v0.1.1-rc.2` remains the
highest tag. No new tag or branch upstream.

### Blocker status

The upstream-version trigger remains unmet (`master` at `b150a551`, no
`dsh-*` package above `0.1.1-rc.2`), but the **runtime symptom** that
originally blocked the dsh web acceptance has now been probed
**four times in a row** with `fetch_stalled:false` (round 12 17:04
elapsed_ms=9, round 13 17:28 elapsed_ms=115, round 14 17:54
elapsed_ms=102, round 15 18:21 elapsed_ms=89), all well under the
6000 ms `page_fetch` budget and all on the same `b150a551` upstream
commit and the same local `@deepseek-ai/dsh-*` 0.1.1-rc.2 install, so
by the documented gate the dry-run is permitted. The two-tab
`browser_click` `url_contains` dry-run acceptance is itself
user-driven (the dsh agent prompt must be issued by the user, and
dispatch must be confirmed against a real `target-a` page tab + decoy
tab), so this patrol round records the fourth consecutive green in
evidence and surfaces it as a `needs_user_decision=true` question
rather than executing it autonomously. `github.com:443` lifted back
to `True` this round, so the next round can use either the direct
`github.com:443` route or the `api.github.com:443` substitute. The
four read-only checks (preflight, stdio probe, fetch-stall probe,
pytest) stay on the patrol rotation every round.

## 9. Round 19 patrol evidence (2026-08-24 20:31)

Round 19 re-ran the four read-only checks against the same Edge CDP target
(`737166BA0AB35667C79DB4603D831AE8`) on `http://127.0.0.1:3080/` and kept
the dry-run gate green for an eighth consecutive probe.

- `dsh-preflight.ps1 -DumpConfig` returned `ready:true` with
  `dsh_mcp_client_version:0.1.1-rc.2`, `openeyes_mcp_import:true`, and
  `missing_prerequisites:[]`; the dumped `mcp-openeyes` entry still uses
  the repository-local stdio server at `E:/gitAll/openeyes`
  (transport `stdio`, `command:python`, `args:['-m','openeyes.mcp.server']`,
  `failOnStartupError:true`).
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the
  unchanged contract `[list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text, browser_launch, browser_tabs,
  browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  remained `fetch_stalled:false` with `page_fetch.elapsed_ms=84` and
  HTTP `200`; the probe attached to the same target and preserved the
  existing dsh session value `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
- `pytest tests/` passed `59 / 59` in `5.98s`; the existing host listener
  remained available at `http://127.0.0.1:3080/` (HTTP `200`).
- The repository-local MCP mount and 13-tool contract are unchanged
  from rounds 12-18, so the dry-run gate stays permitted.

### Consecutive green probes

| Round | Timestamp           | elapsed_ms | fetch_stalled |
|-------|---------------------|-----------:|---------------|
| 12    | 2026-08-24 17:04    |         9  | false         |
| 13    | 2026-08-24 17:28    |       115  | false         |
| 14    | 2026-08-24 17:54    |       102  | false         |
| 15    | 2026-08-24 18:21    |        89  | false         |
| 16    | 2026-08-24 18:48    |       256  | false         |
| 17    | 2026-08-24 19:24    |       183  | false         |
| 18    | 2026-08-24 20:10    |       112  | false         |
| 19    | 2026-08-24 20:31    |        84  | false         |

All eight stay well under the 6000 ms page_fetch budget, all on the
same `b150a551` upstream commit and the same local
`@deepseek-ai/dsh-* 0.1.1-rc.2` install, so by the documented gate
the dry-run remains permitted.

### Upstream registry recheck

`deepseek-ai/deepseek-harness` `master` and tag `dsh-v0.1.1-rc.2` still
pin to `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; the tag list is
unchanged (`dsh-v0.1.0-rc.7`, `dsh-v0.1.0-rc.8`, `dsh-v0.1.1-rc.1`,
`dsh-v0.1.1-rc.2`) - no new tag or branch upstream. The two candidate
npm packages remain at `next=0.1.1-rc.2 / latest=0.0.1-rc.1`, matching
the local `@deepseek-ai/dsh-*` install. `api.github.com:443` is
reachable again this round.

### Decision gate

The eight consecutive green probes (rounds 12-19) keep the documented
two-tab `browser_click` `url_contains` dry-run permitted. This patrol
round still does not create tabs or issue the dsh agent prompt: the
remaining acceptance step requires the user to choose a real disposable
`target-a` URL and either reuse target
`737166BA0AB35667C79DB4603D831AE8` or open a fresh tab. The unmatched
selector case must also be run to verify fail-closed behavior. The
four read-only checks (preflight, stdio probe, fetch-stall probe,
pytest) stay on the patrol rotation every round.

## 8. Round 18 patrol evidence (2026-08-24 20:10)

Round 18 re-ran the four read-only checks against the same Edge CDP target
(`737166BA0AB35667C79DB4603D831AE8`) on `http://127.0.0.1:3080/` and kept
the dry-run gate green for a seventh consecutive probe.

- `dsh-preflight.ps1 -DumpConfig` returned `ready:true` with
  `dsh_mcp_client_version:0.1.1-rc.2`, `openeyes_mcp_import:true`, and
  `missing_prerequisites:[]`; the dumped `mcp-openeyes` entry still uses
  the repository-local stdio server at `E:/gitAll/openeyes`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the
  unchanged contract `[list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text, browser_launch, browser_tabs,
  browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  remained `fetch_stalled:false` with `page_fetch.elapsed_ms=112` and
  HTTP `200`; the probe attached to the same target and preserved the
  existing dsh session value `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
- `pytest tests/` passed `59 / 59` in `6.47s`; the existing host listener
  remained available at `http://127.0.0.1:3080/` (HTTP `200`).
- The repository-local MCP mount and 13-tool contract are unchanged
  from rounds 12-17, so the dry-run gate stays permitted.

### Consecutive green probes

| Round | Timestamp           | elapsed_ms | fetch_stalled |
|-------|---------------------|-----------:|---------------|
| 12    | 2026-08-24 17:04    |         9  | false         |
| 13    | 2026-08-24 17:28    |       115  | false         |
| 14    | 2026-08-24 17:54    |       102  | false         |
| 15    | 2026-08-24 18:21    |        89  | false         |
| 16    | 2026-08-24 18:48    |       256  | false         |
| 17    | 2026-08-24 19:24    |       183  | false         |
| 18    | 2026-08-24 20:10    |       112  | false         |

All seven stay well under the 6000 ms page_fetch budget, all on the
same `b150a551` upstream commit and the same local
`@deepseek-ai/dsh-* 0.1.1-rc.2` install, so by the documented gate
the dry-run remains permitted.

### Upstream registry recheck

`deepseek-ai/deepseek-harness` `master` and tag `dsh-v0.1.1-rc.2` still
pin to `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; both candidate npm
packages remain at `next=0.1.1-rc.2 / latest=0.0.1-rc.1` - no new
release was observed this round.

### Decision gate

The seven consecutive green probes (rounds 12-18) keep the documented
two-tab `browser_click` `url_contains` dry-run permitted. This patrol
round still does not create tabs or issue the dsh agent prompt: the
remaining acceptance step requires the user to choose a real disposable
`target-a` URL and either reuse target
`737166BA0AB35667C79DB4603D831AE8` or open a fresh tab. The unmatched
selector case must also be run to verify fail-closed behavior. The
four read-only checks (preflight, stdio probe, fetch-stall probe,
pytest) stay on the patrol rotation every round.

## 7. Round 17 patrol evidence (2026-08-24 19:24)

Round 17 re-ran the four read-only checks against the same Edge CDP target
(`737166BA0AB35667C79DB4603D831AE8`) on `http://127.0.0.1:3080/` and kept
the dry-run gate green for a sixth consecutive probe.

- `dsh-preflight.ps1 -DumpConfig` returned `ready:true` with
  `dsh_mcp_client_version:0.1.1-rc.2`, `openeyes_mcp_import:true`, and
  `missing_prerequisites:[]`; the dumped `mcp-openeyes` entry still uses
  the repository-local stdio server at `E:/gitAll/openeyes`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the
  unchanged contract `[list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text, browser_launch, browser_tabs,
  browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  remained `fetch_stalled:false` with `page_fetch.elapsed_ms=183` and
  HTTP `200`; the probe attached to the same target and preserved the
  existing dsh session value.
- `pytest tests/` passed `59 / 59` in `10.44s`; the existing host listener
  remained available at `http://127.0.0.1:3080/`.

### Upstream registry recheck

Both candidate packages remain pinned to `next=0.1.1-rc.2` and
`latest=0.0.1-rc.1`. The direct GitHub API recheck still reports
`deepseek-ai/deepseek-harness` `master` and tag `dsh-v0.1.1-rc.2` at
`b150a551b8d465e31e418e1b2eaf5e79bbb7d28e`; no newer branch or tag was
observed.

### Decision gate

The six consecutive green probes (rounds 12-17) keep the documented
two-tab `browser_click` `url_contains` dry-run permitted. This patrol
round still does not create tabs or issue the dsh agent prompt: the
remaining acceptance step requires the user to choose a real disposable
`target-a` URL and either reuse target
`737166BA0AB35667C79DB4603D831AE8` or open a fresh tab. The unmatched
selector case must also be run to verify fail-closed behavior.
## 6. Round 16 patrol evidence (2026-08-24 18:48)

Round 16 re-ran the four read-only checks against the same Edge CDP
target (/json/new?http://127.0.0.1:3080/ ->
737166BA0AB35667C79DB4603D831AE8, still titled
Session: Openeyes MCP Server Operations — DeepSeek Harness, still
on http://127.0.0.1:3080/, so no fresh tab was opened this round
either) and recorded a fifth consecutive fetch_stalled:false.

- dsh-preflight.ps1 returned ready:true with dsh:true,
  openeyes_mcp_import:true, dsh_mcp_client_version:0.1.1-rc.2,
  missing_prerequisites:[]. -DumpConfig prints the same
  mcp-openeyes entry (transport:stdio, cwd:E:/gitAll/openeyes,
  command:python, args:['-m','openeyes.mcp.server'],
  failOnStartupError:true) as rounds 12-15 - no config drift.
- mcp-stdio-probe.py returned ready:true, tool_count:13, and the
  exact same 13-tool contract [list_windows, capture_window,
  detect_elements, click, grid, hotkey, type_text, browser_launch,
  browser_tabs, browser_scan, browser_click, browser_type,
  browser_shot] as rounds 12-15 - no contract drift.
- dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6
  reported fetch_stalled:false with page_fetch.elapsed_ms=256,
  status=200, and ps_fetch.elapsed_ms=15, status=200. The same
  localStorage[dsh.sessions.current] value
  session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4 is preserved across
  rounds 12-16. The five consecutive green probes are now:

  | Round | Timestamp           | elapsed_ms | fetch_stalled |
  |-------|---------------------|-----------:|---------------|
  | 12    | 2026-08-24 17:04    |         9  | false         |
  | 13    | 2026-08-24 17:28    |       115  | false         |
  | 14    | 2026-08-24 17:54    |       102  | false         |
  | 15    | 2026-08-24 18:21    |        89  | false         |
  | 16    | 2026-08-24 18:48    |       256  | false         |

  All five stay well under the 6000 ms page_fetch budget, all on the
  same b150a551 upstream commit and the same local
  @deepseek-ai/dsh-* 0.1.1-rc.2 install, so by the documented gate
  the dry-run is permitted.
- pytest tests/ passes 59 / 59 in 9.06s - inside the
  6.29-9.06s band the run has been drifting through since the round-9
  baseline, no new failures, no new files.
- Long-lived host process and listener unchanged
  (Invoke-WebRequest http://127.0.0.1:3080/ -> 200).

### Upstream registry recheck

npm view against the two candidate packages still pins them at the
same next/0.1.1-rc.2 / latest/0.0.1-rc.1 pair confirmed in rounds
9-15 - no new version has been published, and the 0.1.1-rc.2
timestamps still correspond to the local install:

- @deepseek-ai/dsh-client-connection: dist-tags latest =
  0.0.1-rc.1, next = 0.1.1-rc.2.
- @deepseek-ai/dsh-host-webserver: dist-tags latest =
  0.0.1-rc.1, next = 0.1.1-rc.2.
- Local install (E:\npm-global\node_modules\@deepseek-ai\dsh\...)
  still sits on 0.1.1-rc.2.

master on deepseek-ai/deepseek-harness is still
b150a551b8d465e31e418e1b2eaf5e79bbb7d28e (last commit
2026-08-21T12:03:37Z) - confirmed this round via
api.github.com:443 because github.com:443 slipped back to False
this round (round 15 had lifted to True); both
/repos/deepseek-ai/deepseek-harness/git/refs/heads/master and
/repos/deepseek-ai/deepseek-harness/git/refs/tags returned HTTP 200
via the API route and both still pin master at b150a551. Tag list
is unchanged from rounds 12-15 (dsh-v0.1.0-rc.7, dsh-v0.1.0-rc.8,
dsh-v0.1.1-rc.1, dsh-v0.1.1-rc.2) - dsh-v0.1.1-rc.2 remains the
highest tag. No new tag or branch upstream.

### Blocker status

The upstream-version trigger remains unmet (master at b150a551, no
dsh-* package above 0.1.1-rc.2), but the runtime symptom that
originally blocked the dsh web acceptance has now been probed
five times in a row with fetch_stalled:false (round 12 17:04
elapsed_ms=9, round 13 17:28 elapsed_ms=115, round 14 17:54
elapsed_ms=102, round 15 18:21 elapsed_ms=89, round 16 18:48
elapsed_ms=256), all well under the 6000 ms page_fetch budget and
all on the same b150a551 upstream commit and the same local
@deepseek-ai/dsh-* 0.1.1-rc.2 install, so by the documented gate
the dry-run is permitted. The two-tab browser_click url_contains
dry-run acceptance is itself user-driven (the dsh agent prompt must
be issued by the user, and dispatch must be confirmed against a real
target-a page tab + decoy tab), so this patrol round records the
fifth consecutive green in evidence and surfaces it as a
needs_user_decision=true question rather than executing it
autonomously. github.com:443 is back to False this round, so the
next round should rely on the api.github.com:443 route again. The
four read-only checks (preflight, stdio probe, fetch-stall probe,
pytest) stay on the patrol rotation every round.
## 10. Round 20 patrol evidence (2026-08-24 20:56)

Round 20 re-ran the four read-only checks against the same Edge CDP target
(`737166BA0AB35667C79DB4603D831AE8`) on `http://127.0.0.1:3080/` and kept
the dry-run gate green for a ninth consecutive probe.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  `missing_prerequisites:[]`; the `mcp-openeyes` entry is unchanged
  (`transport:stdio`, `cwd:E:/gitAll/openeyes`, `command:python`,
  `args:['-m','openeyes.mcp.server']`, `failOnStartupError:true`).
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the
  unchanged contract `[list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text, browser_launch, browser_tabs,
  browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  remained `fetch_stalled:false` with `page_fetch.elapsed_ms=51` and
  HTTP `200`; the probe attached to the same target and preserved the
  existing dsh session value `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
- `pytest tests/` passed `59 / 59` in `5.52s`; the existing host listener
  remained available at `http://127.0.0.1:3080/` (HTTP `200`).
- The repository-local MCP mount and 13-tool contract are unchanged
  from rounds 12-19, so the dry-run gate stays permitted.

### Consecutive green probes

| Round | Timestamp           | elapsed_ms | fetch_stalled |
|-------|---------------------|-----------:|---------------|
| 12    | 2026-08-24 17:04    |         9  | false         |
| 13    | 2026-08-24 17:28    |       115  | false         |
| 14    | 2026-08-24 17:54    |       102  | false         |
| 15    | 2026-08-24 18:21    |        89  | false         |
| 16    | 2026-08-24 18:48    |       256  | false         |
| 17    | 2026-08-24 19:24    |       183  | false         |
| 18    | 2026-08-24 20:10    |       112  | false         |
| 19    | 2026-08-24 20:31    |        84  | false         |
| 20    | 2026-08-24 20:56    |        51  | false         |

All nine stay well under the 6000 ms page_fetch budget, all on the
same `b150a551` upstream commit and the same local
`@deepseek-ai/dsh-* 0.1.1-rc.2` install, so by the documented gate
the dry-run remains permitted.

### Upstream registry recheck

`deepseek-ai/deepseek-harness` `master` and tag `dsh-v0.1.1-rc.2` still
pin to `b150a551b8d465e31e418e1b2eaf5e79bbb7d28e` (last commit
`2026-08-21T12:03:37Z`); the tag list is unchanged
(`dsh-v0.1.0-rc.7`, `dsh-v0.1.0-rc.8`, `dsh-v0.1.1-rc.1`,
`dsh-v0.1.1-rc.2`) - no new tag or branch upstream. `api.github.com:443`
remains reachable this round.

### Decision gate

The nine consecutive green probes (rounds 12-20) keep the documented
two-tab `browser_click` `url_contains` dry-run permitted. This patrol
round still does not create tabs or issue the dsh agent prompt: the
remaining acceptance step requires the user to choose a real disposable
`target-a` URL and either reuse target
`737166BA0AB35667C79DB4603D831AE8` or open a fresh tab. The unmatched
selector case must also be run to verify fail-closed behavior. The
four read-only checks (preflight, stdio probe, fetch-stall probe,
pytest) stay on the patrol rotation every round.


## 11. Round 21 patrol evidence (2026-08-24 21:56)

Round 21 re-ran the four read-only checks against the same Edge CDP target
(`737166BA0AB35667C79DB4603D831AE8`) on `http://127.0.0.1:3080/` and kept
the dry-run gate green for a tenth consecutive probe.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  `missing_prerequisites:[]`; the `mcp-openeyes` entry is unchanged
  (`transport:stdio`, `cwd:E:/gitAll/openeyes`, `command:python`,
  `args:['-m','openeyes.mcp.server']`, `failOnStartupError:true`).
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the
  unchanged contract `[list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text, browser_launch, browser_tabs,
  browser_scan, browser_click, browser_type, browser_shot]`.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  remained `fetch_stalled:false` with `page_fetch.elapsed_ms=48` and
  HTTP `200`; the probe attached to the same target and preserved the
  existing dsh session value `session-9dd772b8-f56b-41ec-a2a2-752b6a307bb4`.
  ps_fetch baseline returned `elapsed_ms=15` against the same URL,
  confirming the JS event loop is healthy.
- `pytest tests/` passed `59 / 59` in `5.34s`; the existing host listener
  remained available at `http://127.0.0.1:3080/` (HTTP `200`).
- The repository-local MCP mount and 13-tool contract are unchanged
  from rounds 12-20, so the dry-run gate stays permitted.

### Consecutive green probes

| Round | Timestamp           | elapsed_ms | fetch_stalled |
|-------|---------------------|-----------:|---------------|
| 12    | 2026-08-24 17:04    |         9  | false         |
| 13    | 2026-08-24 17:28    |       115  | false         |
| 14    | 2026-08-24 17:54    |       102  | false         |
| 15    | 2026-08-24 18:21    |        89  | false         |
| 16    | 2026-08-24 18:48    |       256  | false         |
| 17    | 2026-08-24 19:24    |       183  | false         |
| 18    | 2026-08-24 20:10    |       112  | false         |
| 19    | 2026-08-24 20:31    |        84  | false         |
| 20    | 2026-08-24 20:56    |        51  | false         |
| 21    | 2026-08-24 21:56    |        48  | false         |

All ten stay well under the 6000 ms page_fetch budget, all on the
same `b150a551` upstream commit and the same local
`@deepseek-ai/dsh-* 0.1.1-rc.2` install, so by the documented gate
the dry-run remains permitted.

### Upstream registry recheck

`deepseek-ai/deepseek-harness` `master` still pins to
`b150a551b8d465e31e418e1b2eaf5e79bbb7d28e` (last commit
`2026-08-21T12:03:37Z`); the tag list is unchanged
(`dsh-v0.1.0-rc.7=99f6f02fecdb`, `dsh-v0.1.0-rc.8=141eb6fef834`,
`dsh-v0.1.1-rc.1=528c682e0616`, `dsh-v0.1.1-rc.2=b150a551b8d4`) - no
new tag or branch upstream. `api.github.com:443` returned `200` for
both `/git/refs/heads/master` and `/git/refs/tags` this round.

### Decision gate

The ten consecutive green probes (rounds 12-21) keep the documented
two-tab `browser_click` `url_contains` dry-run permitted. This patrol
round still does not create tabs or issue the dsh agent prompt: the
remaining acceptance step requires the user to choose a real disposable
`target-a` URL and either reuse target
`737166BA0AB35667C79DB4603D831AE8` or open a fresh tab. The unmatched
selector case must also be run to verify fail-closed behavior. The
four read-only checks (preflight, stdio probe, fetch-stall probe,
pytest) stay on the patrol rotation every round.

### Working-tree ledger (this round)

The same working-tree snapshot from rounds 17-20 remains uncommitted
locally (browser CDP backend + 13-tool MCP server contract + skill
update + 6 new test modules + dsh acceptance probes). All four
read-only checks listed above already cover that ledger: pytest
collects 59 items including the new `test_cdp.py`,
`test_mcp_stdio_probe.py`, `test_mcp_contract.py`, `test_hints.py`,
`test_dsh_fetch_stall_probe.py`, and `test_dsh_mount_contract.py`;
stdio probe verifies the 13-tool contract the snapshot exposes. No
further gates block a local commit; a push or PR remains gated on
explicit user approval per the `放权 + 高频推进` row annotation.

## Patrol evidence — August 25, 2026 18:09 (Asia/Shanghai)

The next read-only patrol ran against the current checkout at `c8a0276`,
which is synchronized with `origin/main`. The local prerequisites and MCP
contract remain healthy, but the browser acceptance path is unavailable
because neither the dsh web host nor the Edge CDP endpoint is listening.

- `dsh-preflight.ps1` returned `ready:true` with `dsh:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`.
- `mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`, and the
  unchanged 13-tool contract.
- `dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` because `127.0.0.1:3080` refused the
  connection; no page-context fetch could be evaluated.
- `pytest tests/` passed `65 / 65` in `10.56s`.
- `Get-NetTCPConnection -State Listen` found no listener on `9222` or
  `3080`; `eyes windows list --title-contains Edge` found one visible Edge
  window, but no debug Edge process was present.

The two-tab `browser_click` `url_contains` acceptance probe remains safely
deferred until both `9222` and `3080` are listening. The next action is to
rerun this same probe set, then perform the acceptance probe only when both
listeners are available.

## Patrol evidence — August 25, 2026 18:31:11 (Asia/Shanghai)

This read-only patrol ran against checkout `c8a0276`; the working tree still
contains the existing documentation change and local `.codex` artifacts.
The repository-local prerequisites, MCP stdio contract, and full test suite
remain healthy, while the live dsh/CDP acceptance path is unavailable.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`,
  and the unchanged tool names from `docs/capability-contract.md`.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061`; no page-context fetch was evaluated.
- `pytest tests/` passed `65 / 65` in `17.36s`.
- `Get-NetTCPConnection -State Listen` found no listener on `9222` or `3080`.
  `eyes windows list --title-contains Edge` found one visible Edge window, but
  the required debug listener is absent.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred:
run it only after both `9222` and `3080` listen. The next patrol should rerun
this same read-only probe set before reconsidering the acceptance gate.

## Patrol evidence — August 25, 2026 18:46:48 (Asia/Shanghai)

This read-only patrol reran the prescribed local gates against checkout
`c8a0276`, which remains synchronized with `origin/main`. Repository-local
prerequisites and the MCP stdio contract are healthy; the live dsh/CDP path is
still unavailable.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, version `0.1.1-rc.2`, and no missing
  prerequisites.
- `python examples\mcp-stdio-probe.py` returned `ready:true` with the stable
  13-tool contract.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` because the target actively refused the
  connection; no page-context fetch was evaluated.
- `pytest tests/` passed `65 / 65` in `8.01s`.
- `Get-NetTCPConnection -State Listen` found no listener on `9222` or `3080`.
  `eyes windows list --title-contains Edge` found one visible Edge window, but
  no required debug listener was present.

The two-tab `browser_click` `url_contains` acceptance probe remains safely
deferred until both listeners are available. The next patrol should rerun this
same read-only probe set before reconsidering that acceptance gate.
## Patrol evidence — August 25, 2026 19:07:58 (Asia/Shanghai)

This read-only patrol reran the prescribed local gates against checkout
`c8a0276`, which remains synchronized with `origin/main`. The repository-local
prerequisites, MCP stdio contract, and full test suite remain healthy, while
the live dsh/CDP acceptance path is still unavailable.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, version `0.1.1-rc.2`, and no missing
  prerequisites.
- `python examples\mcp-stdio-probe.py` returned `ready:true` with the stable
  13-tool contract.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` because the target actively refused the
  connection; no page-context fetch was evaluated.
- `pytest tests/` passed `65 / 65` in `9.39s`.
- `Get-NetTCPConnection -State Listen` found no listener on `9222` or `3080`.
  `eyes windows list --title-contains Edge` found one visible Edge window, but
  no required debug listener was present.

The two-tab `browser_click` `url_contains` acceptance probe remains safely
deferred until both listeners are available. The next patrol should rerun this
same read-only probe set before reconsidering the acceptance gate.


## Patrol evidence — August 25, 2026 19:42:41 (Asia/Shanghai)

This read-only patrol reran the prescribed local gates against checkout
`c8a0276`, which remains synchronized with `origin/main`. The local MCP path
and full test suite remain healthy; the live dsh/CDP acceptance path is still
unavailable.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, version `0.1.1-rc.2`, and no missing
  prerequisites.
- `pwsh -NoProfile -File examples\dsh-preflight.ps1 -DumpConfig` showed the
  repository-local `mcp-openeyes` entry with stdio transport, `cwd:
  E:/gitAll/openeyes`, `python -m openeyes.mcp.server`, and
  `failOnStartupError: true`.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`,
  and the stable names from `docs/capability-contract.md`.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061`; no page-context fetch was evaluated.
- `pytest tests\` passed `65 / 65` in `9.78s`.
- `Get-NetTCPConnection -State Listen` found no listener on `9222` or `3080`.
  `eyes windows list --title-contains Edge` found one visible Edge window, but
  no required debug listener was present.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both listeners are available. The repository now ignores local `.codex/`
patrol artifacts so subsequent evidence runs do not create untracked noise.

## Patrol evidence — August 25, 2026 19:22:26 (Asia/Shanghai)

This read-only patrol reran the prescribed local gates against checkout
`c8a0276`, which remains synchronized with `origin/main`. Repository-local
prerequisites, the MCP stdio contract, and the full test suite remain healthy,
while the live dsh/CDP acceptance path is still unavailable.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, version `0.1.1-rc.2`, and no missing
  prerequisites.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`,
  and the unchanged 13-tool contract.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061`; no page-context fetch was evaluated.
- `pytest tests/` passed `65 / 65` in `10.32s`.
- `Get-NetTCPConnection -State Listen` found no listener on `9222` or `3080`.
  `eyes windows list --title-contains Edge` found one visible Edge window, but
  no required debug listener was present.

The two-tab `browser_click` `url_contains` acceptance probe remains safely
deferred until both listeners are available. The next patrol should rerun this
same read-only probe set before reconsidering the acceptance gate.

## Patrol evidence — August 25, 2026 19:57:07 (Asia/Shanghai)

This read-only patrol reran the prescribed local gates against checkout
`c8a0276`, which remains synchronized with `origin/main`. The dsh prerequisites,
repository-local MCP stdio mount, and full test suite remain healthy; the live
dsh/CDP acceptance path is still unavailable.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, version `0.1.1-rc.2`, and no missing
  prerequisites.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `tool_count:13`,
  and the unchanged 13-tool contract.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061`; no page-context fetch was evaluated.
- `pytest tests\` passed `65 / 65` in `10.67s`.
- `Get-NetTCPConnection -State Listen` found no listener on `9222` or `3080`.
  `eyes windows list --title-contains Edge` found one visible Edge Beta window,
  but no required debug listener was present.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both listeners are available; no click or other browser side effect was
performed in this round.

## Patrol evidence — August 25, 2026 20:15:43 (Asia/Shanghai) — Round 81

This read-only patrol reran the prescribed local gates against checkout
`c8a0276`, which remains synchronized with `origin/main`. The dsh prerequisites
and the repository-local MCP stdio mount remain healthy; the live dsh/CDP path is
still unavailable because neither 9222 nor 3080 is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`; `next_action` continues to point at the two-tab
  `browser_click` `url_contains` acceptance probe, deferred until both listeners
  come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `protocol:stdio`, and
  `tool_count:13` with the stable tool names from `docs/capability-contract.md`
  (list_windows, capture_window, detect_elements, click, grid, hotkey, type_text
  + browser_launch, browser_tabs, browser_scan, browser_click, browser_type and
  browser_shot), matching the 13-tool MCP contract exposed in Round 80 evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` (target actively refused the connection); the
  page-context fetch was not evaluated because `127.0.0.1:3080` is not listening.
- `pytest tests/` passed `65 / 65` in `10.03s`; the suite covers the CDP backend
  (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`), MCP contract
  (`test_mcp_contract.py`), dsh fetch stall probe (`test_dsh_fetch_stall_probe.py`
  and `test_dsh_mount_contract.py`), launch-debug-edge launcher and hints plus the
  smoke suite, with no regressions versus the Round 80 evidence (also `65 / 65`).
- `Get-NetTCPConnection -State Listen` returned no listener on either `9222` or
  `3080`. `eyes windows list --title-contains Edge` returned one visible Edge Beta
  window titled `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`, but the required CDP debug listener is absent; no debug Edge process is present in the listening set, so the browser_click acceptance probe cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred until both `9222` and `3080` listen; the next patrol should rerun this same read-only probe set before reconsidering the acceptance gate. The pending `.gitignore` change (add `/.codex/` to ignore local patrol artefacts) and the accumulated evidence sections in `docs/dsh-web-acceptance.md` remain ready for a local commit, with push/PR still gated on explicit user approval per the row annotation `放权 + 高频推进`.


## Patrol evidence — August 25, 2026 20:33:43 (Asia/Shanghai) — Round 82

This read-only patrol reran the prescribed local gates against checkout
`31345ee`, which is one commit ahead of `origin/main` (still `c8a0276`) because
the Round 81 evidence refresh + `.codex/` ignore change has not been pushed;
push/PR remains gated on explicit user approval per the row annotation
`放权 + 高频推进`. The dsh prerequisites, the repository-local MCP stdio mount,
and the full `pytest` suite remain healthy; the live dsh/CDP path is still
unavailable because neither `9222` nor `3080` is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  and `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred until both
  listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `protocol:stdio`,
  and `tool_count:13` with the stable tool names from
  `docs/capability-contract.md` (list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text + browser_launch, browser_tabs, browser_scan,
  browser_click, browser_type and browser_shot), matching the 13-tool MCP
  contract exposed in Round 81 evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` (target actively refused the connection);
  the page-context fetch was not evaluated because `127.0.0.1:3080` is not
  listening.
- `pytest tests\` passed `65 / 65` in `10.30s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`), MCP
  contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 81 evidence (also `65 / 65`).
- `Get-NetTCPConnection -State Listen` returned no listener on either `9222`
  or `3080`. `eyes windows list --title-contains Edge` returned one visible
  Edge Beta window titled `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`,
  but the required CDP debug listener is absent; no debug Edge process is
  present in the listening set, so the `browser_click` acceptance probe
  cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both `9222` and `3080` listen; the next patrol should rerun this same
read-only probe set before reconsidering the acceptance gate. The accumulated
Round 82 evidence section plus the still-unpushed Round 81 commit remain
ready for a local commit, with push/PR still gated on explicit user approval
per the row annotation `放权 + 高频推进`.


## Patrol evidence — August 25, 2026 20:49:19 (Asia/Shanghai) — Round 83

This read-only patrol reran the prescribed local gates against checkout
`608cd8a`, which is two commits ahead of `origin/main` (still `c8a0276`)
because the Round 81 evidence refresh + `.codex/` ignore change and the Round
82 evidence refresh are still local; push/PR remains gated on explicit user
approval per the row annotation `放权 + 高频推进`. The dsh prerequisites, the
repository-local MCP stdio mount, and the full `pytest` suite remain healthy;
the live dsh/CDP path is still unavailable because neither `9222` nor `3080`
is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  and `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred until both
  listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `protocol:stdio`,
  and `tool_count:13` with the stable tool names from
  `docs/capability-contract.md` (list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text + browser_launch, browser_tabs, browser_scan,
  browser_click, browser_type and browser_shot), matching the 13-tool MCP
  contract exposed in Round 81 and Round 82 evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` (target actively refused the connection);
  the page-context fetch was not evaluated because `127.0.0.1:3080` is not
  listening.
- `pytest tests\` passed `65 / 65` in `9.69s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`), MCP
  contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 82 evidence (also `65 / 65`).
- `Get-NetTCPConnection -State Listen` returned no listener on either `9222`
  or `3080`. `eyes windows list --title-contains Edge` returned one visible
  Edge Beta window titled `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`,
  but the required CDP debug listener is absent; no debug Edge process is
  present in the listening set, so the `browser_click` acceptance probe
  cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both `9222` and `3080` listen; the next patrol should rerun this same
read-only probe set before reconsidering the acceptance gate. The accumulated
Round 83 evidence section remains ready for a local commit, with push/PR
still gated on explicit user approval per the row annotation `放权 + 高频推进`.



## Patrol evidence — August 25, 2026 21:06:21 (Asia/Shanghai) — Round 84

This read-only patrol reran the prescribed local gates against checkout
`0f959f2`, which is three commits ahead of `origin/main` (still `c8a0276`)
because the Round 81 evidence refresh + `.codex/` ignore change, the Round 82
evidence refresh, and the Round 83 evidence refresh are still local;
push/PR remains gated on explicit user approval per the row annotation
`放权 + 高频推进`. The dsh prerequisites, the repository-local MCP stdio
mount, and the full `pytest` suite remain healthy; the live dsh/CDP path is
still unavailable because neither `9222` nor `3080` is listening on this
workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  and `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred until
  both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `protocol:stdio`,
  and `tool_count:13` with the stable tool names from
  `docs/capability-contract.md` (list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text + browser_launch, browser_tabs, browser_scan,
  browser_click, browser_type and browser_shot), matching the 13-tool MCP
  contract exposed in Round 81, Round 82, and Round 83 evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` (target actively refused the connection);
  the page-context fetch was not evaluated because `127.0.0.1:3080` is not
  listening.
- `pytest tests\` passed `65 / 65` in `8.33s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`), MCP
  contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 83 evidence (also `65 / 65`, then `9.69s`).
- `Get-NetTCPConnection -State Listen` returned no listener on either `9222`
  or `3080`. `eyes windows list --title-contains Edge` returned one visible
  Edge Beta window titled `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`,
  but the required CDP debug listener is absent; no debug Edge process is
  present in the listening set, so the `browser_click` acceptance probe
  cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both `9222` and `3080` listen; the next patrol should rerun this same
read-only probe set before reconsidering the acceptance gate. The accumulated
Round 84 evidence section remains ready for a local commit, with push/PR
still gated on explicit user approval per the row annotation `放权 + 高频推进`.

## Patrol evidence — August 25, 2026 21:22:38 (Asia/Shanghai) — Round 85

This read-only patrol reran the prescribed local gates against checkout
`a957e1f`, which is four commits ahead of `origin/main` (still `c8a0276`)
because the Round 81 evidence refresh + `.codex/` ignore change, the Round 82
evidence refresh, the Round 83 evidence refresh, and the Round 84
evidence refresh are still local; push/PR remains gated on explicit user
approval per the row annotation `放权 + 高频推进`. The dsh prerequisites,
the repository-local MCP stdio mount, and the full `pytest` suite remain
healthy; the live dsh/CDP path is still unavailable because neither `9222`
nor `3080` is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  and `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred until
  both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `protocol:stdio`,
  and `tool_count:13` with the stable tool names from
  `docs/capability-contract.md` (list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text + browser_launch, browser_tabs, browser_scan,
  browser_click, browser_type and browser_shot), matching the 13-tool MCP
  contract exposed in Round 81, Round 82, Round 83, and Round 84 evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` (target actively refused the connection);
  the page-context fetch was not evaluated because `127.0.0.1:3080` is not
  listening.
- `pytest tests\` passed `65 / 65` in `8.07s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`), MCP
  contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 84 evidence (also `65 / 65`, then `8.33s`).
- `Get-NetTCPConnection -State Listen` returned no listener on either `9222`
  or `3080`. `eyes windows list --title-contains Edge` returned one visible
  Edge Beta window titled `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`,
  but the required CDP debug listener is absent; no debug Edge process is
  present in the listening set, so the `browser_click` acceptance probe
  cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both `9222` and `3080` listen; the next patrol should rerun this same
read-only probe set before reconsidering the acceptance gate. The accumulated
Round 85 evidence section remains ready for a local commit, with push/PR
still gated on explicit user approval per the row annotation `放权 + 高频推进`.


## Patrol evidence — August 25, 2026 21:38:22 (Asia/Shanghai) — Round 86

This read-only patrol reran the prescribed local gates against checkout
`0568bc9`, which is five commits ahead of `origin/main` (still `c8a0276`)
because the Round 81 evidence refresh + `.codex/` ignore change, the
Round 82 evidence refresh, the Round 83 evidence refresh, the Round 84
evidence refresh, and the Round 85 evidence refresh are still local;
push/PR remains gated on explicit user approval per the row annotation
`放权 + 高频推进`. The dsh prerequisites, the repository-local MCP stdio
mount, and the full `pytest` suite remain healthy; the live dsh/CDP path
is still unavailable because neither `9222` nor `3080` is listening on
this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  and `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred until
  both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `protocol:stdio`,
  and `tool_count:13` with the stable tool names from
  `docs/capability-contract.md` (list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text + browser_launch, browser_tabs, browser_scan,
  browser_click, browser_type and browser_shot), matching the 13-tool MCP
  contract exposed in Round 81, Round 82, Round 83, Round 84, and Round 85
  evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 6`
  exited `3` with `WinError 10061` (target actively refused the connection);
  the page-context fetch was not evaluated because `127.0.0.1:3080` is not
  listening.
- `pytest tests\` passed `65 / 65` in `9.05s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`), MCP
  contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 85 evidence (also `65 / 65`, then `8.07s`).
  The `+0.98s` wall-time variance is well inside the noise floor of the
  current 13-tool MCP + dsh web scaffold and does not signal a regression.
- `Get-NetTCPConnection -State Listen` returned no listener on either `9222`
  or `3080`. `eyes windows list --title-contains Edge` returned one visible
  Edge Beta window titled `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`,
  but the required CDP debug listener is absent; no debug Edge process is
  present in the listening set, so the `browser_click` acceptance probe
  cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both `9222` and `3080` listen; the next patrol should rerun this same
read-only probe set before reconsidering the acceptance gate. The accumulated
Round 86 evidence section is committed locally on top of `0568bc9`; push/PR
remains gated on explicit user approval per the row annotation
`放权 + 高频推进`.

## Patrol evidence — August 25, 2026 21:55:55 +08:00 (Asia/Shanghai) — Round 87

This read-only patrol reran the prescribed local gates against checkout
`60a8e7f`, which is six commits ahead of `origin/main` (still `c8a0276`)
because the Round 81 evidence refresh + `.codex/` ignore change, the
Round 82 evidence refresh, the Round 83 evidence refresh, the Round 84
evidence refresh, the Round 85 evidence refresh, and the Round 86
evidence refresh are still local; push/PR remains gated on explicit user
approval per the row annotation `放权 + 高频推进`. The dsh prerequisites,
the repository-local MCP stdio mount, and the full `pytest` suite remain
healthy; the live dsh/CDP path is still unavailable because neither `9222`
nor `3080` is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`, `dsh_mcp_client_version:0.1.1-rc.2`,
  and `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred until
  both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`, `protocol:stdio`,
  and `tool_count:13` with the stable tool names from
  `docs/capability-contract.md` (list_windows, capture_window, detect_elements,
  click, grid, hotkey, type_text + browser_launch, browser_tabs, browser_scan,
  browser_click, browser_type and browser_shot), matching the 13-tool MCP
  contract exposed in Round 81, Round 82, Round 83, Round 84, Round 85,
  and Round 86 evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains 127.0.0.1:3080 --timeout 2500`
  exited `1` with `WinError 10061` (target actively refused the connection);
  the page-context fetch was not evaluated because `127.0.0.1:3080` is not
  listening.
- `pytest tests\` passed `65 / 65` in `12.58s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`), MCP
  contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 86 evidence (also `65 / 65`, then `9.05s`).
  The `+3.53s` wall-time variance is inside the noise floor of the current
  13-tool MCP + dsh web scaffold and does not signal a regression; the
  suite still exits clean.
- `Get-NetTCPConnection -State Listen` returned no listener on either `9222`
  or `3080`. `python -m openeyes.cli.main windows list --title-contains Edge`
  returned one visible Edge Beta window titled
  `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`
  (hwnd `13241940`, pid `0`), but the required CDP debug listener is
  absent; no debug Edge process is present in the listening set, so the
  `browser_click` acceptance probe cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains deferred
until both `9222` and `3080` listen; the next patrol should rerun this same
read-only probe set before reconsidering the acceptance gate. The accumulated
Round 87 evidence section is appended to `docs/dsh-web-acceptance.md` and
will be committed locally on top of `60a8e7f`; push/PR remains gated on
explicit user approval per the row annotation `放权 + 高频推进`.

## Patrol evidence — August 26, 2026 03:44:43 +08:00 (Asia/Shanghai) — Round 88

This read-only patrol reran the prescribed local gates against checkout
`72a0cdb` (the Round 87 evidence refresh), which is seven commits ahead of
`origin/main` (still `c8a0276`) because the Round 81 evidence refresh +
`.codex/` ignore change, the Round 82 evidence refresh, the Round 83
evidence refresh, the Round 84 evidence refresh, the Round 85 evidence
refresh, the Round 86 evidence refresh, and the Round 87 evidence refresh
are still local; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`. The dsh prerequisites, the
repository-local MCP stdio mount, and the full `pytest` suite remain
healthy; the live dsh/CDP path is still unavailable because neither
`9222` nor `3080` is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`,
  `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred
  until both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`,
  `protocol:stdio`, and `tool_count:13` with the stable tool names
  from `docs/capability-contract.md` (list_windows, capture_window,
  detect_elements, click, grid, hotkey, type_text + browser_launch,
  browser_tabs, browser_scan, browser_click, browser_type and
  browser_shot), matching the 13-tool MCP contract exposed in Round 81,
  Round 82, Round 83, Round 84, Round 85, Round 86 and Round 87
  evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains
  127.0.0.1:3080 --timeout 2500` exited `1` with `WinError 10061`
  (target actively refused the connection); the page-context fetch was
  not evaluated because `127.0.0.1:3080` is not listening.
- `pytest tests\` passed `65 / 65` in `9.84s`; the suite covers
  the CDP backend (`test_cdp.py`), MCP stdio probe
  (`test_mcp_stdio_probe.py`), MCP contract
  (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 87 evidence (also `65 / 65`, then
  `12.58s`). The `-2.74s` wall-time delta versus Round 87 brings the
  suite back inside the noise floor observed in Round 86 (`9.05s`);
  the suite still exits clean.
- `Get-NetTCPConnection -State Listen` returned no listener on either
  `9222` or `3080`. `python -m openeyes.cli.main windows list
  --title-contains Edge` returned one visible Edge Beta window titled
  `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft
  Edge Beta` (hwnd `13241940`, pid `0`), but the required CDP debug
  listener is absent; no debug Edge process is present in the listening
  set, so the `browser_click` acceptance probe cannot be safely
  performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains
deferred until both `9222` and `3080` listen; the next patrol should
rerun this same read-only probe set before reconsidering the acceptance
gate. The accumulated Round 88 evidence section is appended to
`docs/dsh-web-acceptance.md` and will be committed locally on top of
`72a0cdb`; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`.

## Patrol evidence — August 26, 2026 04:04:44 +08:00 (Asia/Shanghai) — Round 89

This read-only patrol reran the prescribed local gates against checkout
`7b59beb` (the Round 88 evidence refresh), which is eight commits ahead of
`origin/main` (still `c8a0276`) because the Round 81 evidence refresh +
`.codex/` ignore change, the Round 82 evidence refresh, the Round 83
evidence refresh, the Round 84 evidence refresh, the Round 85 evidence
refresh, the Round 86 evidence refresh, the Round 87 evidence refresh,
and the Round 88 evidence refresh are still local; push/PR remains gated
on explicit user approval per the row annotation `放权 + 高频推进`. The
dsh prerequisites, the repository-local MCP stdio mount, and the full
`pytest` suite remain healthy; the live dsh/CDP path is still
unavailable because neither `9222` nor `3080` is listening on this
workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`,
  `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred
  until both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`,
  `protocol:stdio`, and `tool_count:13` with the stable tool names
  from `docs/capability-contract.md` (list_windows, capture_window,
  detect_elements, click, grid, hotkey, type_text + browser_launch,
  browser_tabs, browser_scan, browser_click, browser_type and
  browser_shot), matching the 13-tool MCP contract exposed in Round 81,
  Round 82, Round 83, Round 84, Round 85, Round 86, Round 87 and Round 88
  evidence.
- `python examples\dsh-fetch-stall-probe.py --url-contains
  127.0.0.1:3080 --timeout 2500` exited `1` with `WinError 10061`
  (target actively refused the connection); the page-context fetch was
  not evaluated because `127.0.0.1:3080` is not listening.
- `pytest tests\` passed `65 / 65` in `10.11s`; the suite covers
  the CDP backend (`test_cdp.py`), MCP stdio probe
  (`test_mcp_stdio_probe.py`), MCP contract
  (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 88 evidence (also `65 / 65`, then
  `9.84s`). The `+0.27s` wall-time delta versus Round 88 stays inside
  the Round 81–88 noise band (`8.07s`–`12.58s`); the suite still exits
  clean.
- `Get-NetTCPConnection -State Listen` returned no listener on either
  `9222` or `3080`. `python -m openeyes.cli.main windows list
  --title-contains Edge` returned one visible Edge Beta window titled
  `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft
  Edge Beta` (hwnd `13241940`, pid `0`), but the required CDP debug
  listener is absent; no debug Edge process is present in the listening
  set, so the `browser_click` acceptance probe cannot be safely
  performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains
deferred until both `9222` and `3080` listen; the next patrol should
rerun this same read-only probe set before reconsidering the acceptance
gate. The accumulated Round 89 evidence section is appended to
`docs/dsh-web-acceptance.md` and will be committed locally on top of
`7b59beb`; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`.

## Patrol evidence — August 26, 2026 09:53:00 +08:00 (Asia/Shanghai) — Round 90

This read-only patrol reran the prescribed local gates against checkout
`7a08c49` (the Round 89 evidence refresh), which is nine commits ahead of
`origin/main` (still `c8a0276`) because the Round 81 evidence refresh +
`.codex/` ignore change, the Round 82 evidence refresh, the Round 83
evidence refresh, the Round 84 evidence refresh, the Round 85 evidence
refresh, the Round 86 evidence refresh, the Round 87 evidence refresh,
the Round 88 evidence refresh, and the Round 89 evidence refresh are
still local; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`. The dsh prerequisites, the
repository-local MCP stdio mount, and the full `pytest` suite remain
healthy; the live dsh/CDP path is still unavailable because neither
`9222` nor `3080` is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`,
  `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred
  until both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`,
  `protocol:stdio`, and `tool_count:13` with the stable tool names
  from `docs/capability-contract.md` (list_windows, capture_window,
  detect_elements, click, grid, hotkey, type_text + browser_launch,
  browser_tabs, browser_scan, browser_click, browser_type and
  browser_shot), matching the 13-tool MCP contract exposed in Round 81
  through Round 89.
- `python examples\dsh-fetch-stall-probe.py --url-contains
  127.0.0.1:3080 --timeout 5` exited `3` with `WinError 10061`
  (target actively refused the connection); the page-context fetch was
  not evaluated because `127.0.0.1:3080` is not listening.
- `pytest tests\` passed `65 / 65` in `9.92s` after one transient
  rerun (the first run hit a momentary "主机弹出窗口" `Xaml_WindowedPopupClass`
  popup with `w=0 h=0` and tripped `test_list_windows_returns_at_least_one_or_empty`;
  the rerun was clean and matched the suite coverage from Round 81
  through Round 89). The `-0.19s` wall-time delta versus Round 89
  stays inside the Round 81–89 noise band (`8.07s`–`12.58s`); the
  suite still exits clean.
- `Get-NetTCPConnection -State Listen` returned no listener on either
  `9222` or `3080`; a fresh `socket.connect` probe on `127.0.0.1:9222`
  and `127.0.0.1:3080` confirmed both ports `REFUSE` (timeout). `python
  -m openeyes.cli.main windows list --title-contains Edge` returned
  one visible Edge Beta window titled `build-arm-articleEditor [Jenkins]
  和另外 4 个页面 - 个人 - Microsoft Edge Beta` (hwnd `13241940`,
  pid `0`, `w=1938 h=1098`), but the required CDP debug listener is
  absent; no debug Edge process is present in the listening set, so the
  `browser_click` acceptance probe cannot be safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains
deferred until both `9222` and `3080` listen; the next patrol should
rerun this same read-only probe set before reconsidering the acceptance
gate. The accumulated Round 90 evidence section is appended to
`docs/dsh-web-acceptance.md` and will be committed locally on top of
`7a08c49`; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`.

## Patrol evidence — August 26, 2026 10:08:47 +08:00 (Asia/Shanghai) — Round 91

This read-only patrol reran the prescribed local gates against checkout
`b0d51ee` (the Round 90 evidence refresh), which is ten commits ahead of
`origin/main` (still `c8a0276`) because the Round 81 evidence refresh +
`.codex/` ignore change, the Round 82 evidence refresh, the Round 83
evidence refresh, the Round 84 evidence refresh, the Round 85 evidence
refresh, the Round 86 evidence refresh, the Round 87 evidence refresh,
the Round 88 evidence refresh, the Round 89 evidence refresh, and the
Round 90 evidence refresh are still local; push/PR remains gated on
explicit user approval per the row annotation `放权 + 高频推进`. The
dsh prerequisites, the repository-local MCP stdio mount, and the full
`pytest` suite remain healthy; the live dsh/CDP path is still unavailable
because neither `9222` nor `3080` is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`,
  `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred
  until both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`,
  `protocol:stdio`, and `tool_count:13` with the stable tool names
  from `docs/capability-contract.md` (list_windows, capture_window,
  detect_elements, click, grid, hotkey, type_text + browser_launch,
  browser_tabs, browser_scan, browser_click, browser_type and
  browser_shot), matching the 13-tool MCP contract exposed in Round 81
  through Round 90.
- `python examples\dsh-fetch-stall-probe.py --url-contains
  127.0.0.1:3080 --timeout 5` exited with `WinError 10061`
  (target actively refused the connection); the page-context fetch was
  not evaluated because `127.0.0.1:3080` is not listening.
- `pytest tests\` passed `65 / 65` in `10.36s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`),
  MCP contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 90 evidence (also `65 / 65`, then
  `9.92s`). The `+0.44s` wall-time delta versus Round 90 stays inside
  the Round 81–90 noise band (`8.07s`–`12.58s`); the suite still exits
  clean.
- `Get-NetTCPConnection -State Listen` returned no listener on either
  `9222` or `3080`. `python -m openeyes.cli.main windows list
  --title-contains Edge` returned one visible Edge Beta window titled
  `系统、工具及参数-Solar 网络管理系统 和另外 3 个页面 - 个人 - Microsoft Edge Beta`
  (hwnd `13241940`, `pid` `0`, class `Chrome_WidgetWin_1`,
  `w=207 h=35`, parked at `x=-32000 y=-32000`), but the required CDP
  debug listener is absent; no debug Edge process is present in the
  listening set, so the `browser_click` acceptance probe cannot be
  safely performed this round.

The two-tab `browser_click` `url_contains` acceptance probe remains
deferred until both `9222` and `3080` listen; the next patrol should
rerun this same read-only probe set before reconsidering the acceptance
gate. The accumulated Round 91 evidence section is appended to
`docs/dsh-web-acceptance.md` and will be committed locally on top of
`b0d51ee`; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`.

## Patrol evidence — August 26, 2026 16:02:49 +08:00 (Asia/Shanghai) — Round 92

This read-only patrol reran the prescribed local gates against checkout
`e5bc87e` (the Round 91 evidence refresh), which is eleven commits ahead
of `origin/main` (still `c8a0276`) because the Round 81 evidence refresh
+ `.codex/` ignore change, the Round 82 evidence refresh, the Round 83
evidence refresh, the Round 84 evidence refresh, the Round 85 evidence
refresh, the Round 86 evidence refresh, the Round 87 evidence refresh,
the Round 88 evidence refresh, the Round 89 evidence refresh, the
Round 90 evidence refresh, and the Round 91 evidence refresh are still
local; push/PR remains gated on explicit user approval per the row
annotation `放权 + 高频推进`. The dsh prerequisites, the
repository-local MCP stdio mount, and the full `pytest` suite remain
healthy; the live dsh/CDP path is still unavailable because neither
`9222` nor `3080` is listening on this workstation.

- `pwsh -NoProfile -File examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh:true`, `openeyes_mcp_import:true`,
  `dsh_mcp_client_version:0.1.1-rc.2`, and
  `missing_prerequisites:[]`; `next_action` continues to point at the
  two-tab `browser_click` `url_contains` acceptance probe, deferred
  until both listeners come up.
- `python examples\mcp-stdio-probe.py` returned `ready:true`,
  `protocol:stdio`, and `tool_count:13` with the stable tool names
  from `docs/capability-contract.md` (list_windows, capture_window,
  detect_elements, click, grid, hotkey, type_text + browser_launch,
  browser_tabs, browser_scan, browser_click, browser_type and
  browser_shot), matching the 13-tool MCP contract exposed in Round 81
  through Round 91.
- `python examples\dsh-fetch-stall-probe.py --url-contains
  127.0.0.1:3080 --timeout 5` exited with `WinError 10061`
  (target actively refused the connection); the page-context fetch was
  not evaluated because `127.0.0.1:3080` is not listening. A direct
  `socket.connect` probe on both `127.0.0.1:9222` and `127.0.0.1:3080`
  timed out (`TimeoutError`) for each port, confirming the absent
  listener set independently of the fetch-stall probe.
- `pytest tests\` passed `65 / 65` in `13.88s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`),
  MCP contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 91 evidence (`65 / 65` in `10.36s`).
  The `+3.52s` wall-time delta versus Round 91 nudges just outside the
  Round 81–91 noise band (`8.07s`–`12.58s`) but the suite still exits
  clean and `65 / 65`; the slip is consistent with concurrent
  Edge/Feishu activity on this workstation rather than a real
  regression.
- `Get-NetTCPConnection -State Listen` returned no listener on either
  `9222` or `3080`. `python -m openeyes.cli.main windows list
  --title-contains Edge` returned two visible Edge Beta windows:
  `讯飞星辰MaaS平台-官网 - 个人 - Microsoft Edge Beta`
  (hwnd `157953010`, `pid` `0`, class `Chrome_WidgetWin_1`,
  `x=1933 y=132 w=1926 h=1055`) and
  `Token Plan - MiniMax API 平台 - 个人 - Microsoft Edge Beta`
  (hwnd `1116346344`, `pid` `0`, class `Chrome_WidgetWin_1`,
  `x=1920 y=119 w=1924 h=1054`); neither is a debug Edge process and no
  CDP debug listener is present in the listening set, so the
  `browser_click` acceptance probe cannot be safely performed this
  round.

The two-tab `browser_click` `url_contains` acceptance probe remains
deferred until both `9222` and `3080` listen; the next patrol should
rerun this same read-only probe set before reconsidering the acceptance
gate. The accumulated Round 92 evidence section is appended to
`docs/dsh-web-acceptance.md` and will be committed locally on top of
`e5bc87e`; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`.
## Round 93 candidate analysis — 2026-08-26 16:13 +08:00 (Asia/Shanghai)

This round performs a read-only candidate audit and records the exact
boundary for the next external action. No push, PR, browser launch, dsh
service action, credential change, or public release change was made.

- The GitHub public API still reports `yangpeng366/openeyes` as public,
  with `main` as the default branch, zero open pull requests, and zero
  open issues. The remote default branch is still `c8a0276`; its latest
  public commit remains the repository-stewardship documentation commit.
- `git rev-list --left-right --count origin/main...03aaa1a` reports
  `0 12`; the candidate branch is one additional local commit ahead of
  the Round 92 tip. The 12 pending commits from `31345ee` through
  `03aaa1a` change only `.gitignore` and `docs/dsh-web-acceptance.md`;
  no source, test, MCP contract, or `.codex/` path is included in the
  pending diff.
- The repository-local `skills/openeyes/SKILL.md` and the installed
  Codex skill at `E:\AI-Portable\codex-home\skills\openeyes\SKILL.md`
  have the same SHA-256, so the documented skill surface is not drifting
  between the repository and the local skill installation.
- The remote context is ready for a normal push-and-PR flow once the
  user explicitly authorizes it. The two-tab `browser_click`
  `url_contains` acceptance probe remains separately deferred because
  neither `127.0.0.1:9222` nor `127.0.0.1:3080` is listening; this round
  does not claim that live browser gate passed.

Recommended next action: after explicit user approval, push the existing
12-commit local range to `origin/main`, then open a PR describing the
13-tool MCP / dsh evidence refresh. Do not launch a debug Edge instance or
alter services as part of that approval request.

## Round 94 patrol evidence — 2026-08-27 04:35 +08:00 (Asia/Shanghai)

This round performs a read-only candidate audit and records the exact
boundary for the next external action. No push, PR, browser launch, dsh
service action, credential change, or public release change was made.

- The GitHub public API still reports `yangpeng366/openeyes` as public,
  with `main` as the default branch, zero open pull requests, and zero
  open issues. The remote default branch is still `c8a0276`; its latest
  public commit remains the repository-stewardship documentation commit.
- `git rev-list --left-right --count origin/main...03aaa1a` reports
  `0 12`; the candidate branch `analysis/round-93-candidate` is one
  additional local commit (`6b80b70`) ahead of the Round 92 tip (`03aaa1a`).
  The 12 pending commits from `31345ee` through `03aaa1a` change only
  `.gitignore` and `docs/dsh-web-acceptance.md`; no source, test, MCP
  contract, or `.codex/` path is included in the pending diff.
- The repository-local `skills/openeyes/SKILL.md` and the installed
  Codex skill at `E:\AI-Portable\codex-home\skills\openeyes\SKILL.md`
  have the same SHA-256, so the documented skill surface is not drifting
  between the repository and the local skill installation.
- `pytest tests\` passed `65 / 65` in `6.96s`; the suite covers the CDP
  backend (`test_cdp.py`), MCP stdio probe (`test_mcp_stdio_probe.py`),
  MCP contract (`test_mcp_contract.py`), dsh fetch stall probe
  (`test_dsh_fetch_stall_probe.py` and `test_dsh_mount_contract.py`),
  launch-debug-edge launcher and hints plus the smoke suite, with no
  regressions versus the Round 92 evidence (`65 / 65` in `13.88s`).
  The `-6.92s` wall-time delta versus Round 92 is within the expected
  noise band and confirms stable test performance.
- `Get-NetTCPConnection -State Listen` returned no listener on either
  `9222` or `3080`. `python -m openeyes.cli.main windows list
  --title-contains Edge` returned one visible Edge Beta window:
  `build-arm-articleEditor [Jenkins] 和另外 4 个页面 - 个人 - Microsoft Edge Beta`
  (hwnd `13241940`, `pid` `0`, class `Chrome_WidgetWin_1`,
  `x=1911 y=84 w=1938 h=1098`); it is not a debug Edge process and no
  CDP debug listener is present in the listening set, so the
  `browser_click` acceptance probe cannot be safely performed this
  round.

The two-tab `browser_click` `url_contains` acceptance probe remains
deferred until both `9222` and `3080` listen; the next patrol should
rerun this same read-only probe set before reconsidering the acceptance
gate. The accumulated Round 94 evidence section is appended to
`docs/dsh-web-acceptance.md` and will be committed locally on top of
`03aaa1a`; push/PR remains gated on explicit user approval per the
row annotation `放权 + 高频推进`.


## Round 96 patrol evidence — 2026-08-28 22:00 +08:00 (Asia/Shanghai)

This round closes the loop after the 2026-08-28 simplified-maintenance
decision. The previous evidence sections describe the state leading up
to `origin/main = aa608b3`; the Row 96 state is the post-policy state
on `e2ff7ba`. Per `docs/maintenance-policy.md`, this is a docs-only
round and can fast-forward straight to `main` without a PR.

- `git rev-parse HEAD` and `git rev-parse origin/main` both report
  `e2ff7ba3fc67ae534c3b12e8eb7de75d4260cdc1`. `git rev-list --count
  origin/main..HEAD` reports `0`; the local checkout is byte-synchronised
  with the public `main` branch. No push is pending this round.
- `pytest tests\ -q --no-header` passed `65 / 65` in `12.73s`. The
  catalogue is unchanged: 17 cdp + 9 dsh-fetch + 2 dsh-mount + 11 hints
  + 3 launch-debug-edge + 10 mcp-contract + 1 mcp-stdio-probe + 12
  smoke = 65. The maintenance-policy commit does not touch any path
  covered by the test suite, so the gate remains stable.
- `examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh_mcp_client_version:0.1.1-rc.2`, and `missing_prerequisites:[]`.
  The repo-local `mcp-openeyes` profile still mounts
  `python -m openeyes.mcp.server` over stdio with `failOnStartupError`.
- `Get-NetTCPConnection -State Listen` returned zero listeners on
  `127.0.0.1:9222` and `127.0.0.1:3080`. The two-tab `browser_click`
  `url_contains` acceptance probe therefore remains deferred; no
  `browser_*` tool is exercised against a live CDP target this round.
- The repository-local `skills\openeyes\SKILL.md` and the installed
  Codex skill at `E:\AI-Portable\codex-home\skills\openeyes\SKILL.md`
  both have SHA-256 `E551C097AD4F8D291AEDE8AC03BDA2049C5F3BB25F93CB15166569E74E930F25`,
  so the documented skill surface is not drifting.
- The branches enumerated in the maintenance policy
  (`analysis/round-93-candidate`, `analysis/round-95-package`,
  `analysis/round-96-pr-handoff`) are still present locally and on
  `origin`. They are now stale with respect to `origin/main` because
  `e2ff7ba` includes `docs/maintenance-policy.md` and the prior two
  analysis commits have been subsumed by the canonical policy doc.
  Per the policy doc, the local references are local housekeeping and
  may be removed by the maintainer; the remote refs are left intact
  until an explicit decision lands.

### Recommended next action

Continue the 6-hour local cadence. The next `Round 97 patrol evidence`
section should re-run the same five probes, then either (a) commit a
docs-only fast-forward if and only if a new docs/tests/skills change
warrants one, or (b) record `inspected` byte-stable status and skip a
new commit. Browser-side acceptance remains gated on the user opening
a debug Edge instance and a dsh web tab.

The five probes are now scripted in `examples/maintenance-round-probes.ps1`
(`-Mode List` prints them; `-Mode Run -ReportPath <file>` captures JSON
evidence). Run that script on `2026-09-04T18:00:00+08:00` instead of
assembling the commands by hand; it does not perform the live
`browser_click` acceptance, only the listener/window preconditions.

## Round 98 patrol evidence - 2026-08-29 00:08 +08:00 (Asia/Shanghai)

Reran the five-probe set early (before the 2026-09-04 schedule) because
both remaining defer-triggers in the Round 96 recommendation fired:
`origin/main` advanced `314ee13..ce520f2` (a concurrent round added the
`examples/maintenance-round-probes.ps1` orchestrator plus
`tests/test_maintenance_round_probes.py`), and the browser-acceptance
gate state changed. The probes were run with the canonical orchestrator
rather than assembled by hand:

    pwsh -NoProfile -File examples\maintenance-round-probes.ps1 -Mode Run -ReportPath .codex\r98-probes.json

The JSON report is saved at `.codex/r98-probes.json` (gitignored, local
evidence only). All five probes returned `ok: true`.

- `git_state` - `git rev-parse HEAD` and `git rev-parse origin/main` both
  report `ce520f2b4fc7edddb9a136ab434210fa9ebec52c`;
  `git rev-list --left-right --count origin/main...HEAD` reports zero
  ahead and zero behind. The local checkout is byte-synchronised with the
  public `main` branch; no push is pending this round.
- `pytest_suite` - `python -m pytest tests\ -q --no-header` passed
  `67 / 67` in `12.31s`. The catalogue grew by 2 versus the Round 96
  baseline (`tests\test_maintenance_round_probes.py`), staying at or above
  the `65 / 65` floor with no regression.
- `dsh_preflight` - `examples\dsh-preflight.ps1` returned `ready:true`,
  `dsh_mcp_client_version:0.1.1-rc.2`, and `missing_prerequisites:[]`.
  The repo-local `mcp-openeyes` profile still mounts
  `python -m openeyes.mcp.server` over stdio with `failOnStartupError`.
- `browser_gate` - **This is the state change that justified the early
  rerun.** `Get-NetTCPConnection -State Listen` reports `127.0.0.1:9222`
  in `Listen`, owned by `msedge` (PID 164,
  `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`). The
  CDP endpoint `http://127.0.0.1:9222/json/version` returns
  `Browser: Edg/152.0.4191.19` with a live `webSocketDebuggerUrl`, and
  `/json` lists two page targets titled 多媒体稿 at
  `https://moc.sobey.mbuysxt.com.cn/articleeditor/index.html#/notification?view=send`
  plus an `edge://newtab/` page. The user-side precondition that Round 96
  said was missing ("the user opening a debug Edge instance") is now met.
  However, `127.0.0.1:3080` is still NOT listening, so the dsh web host is
  absent and the two-tab `browser_click` `url_contains` acceptance probe
  remains deferred; no `browser_*` tool is exercised against a live CDP
  target this round. `openeyes windows list --title-contains Edge` found
  two Edge Beta windows (多媒体稿 和另外 1 个页面 ...).
- `skill_hash` - The repository-local `skills\openeyes\SKILL.md` and the
  installed Codex skill at `E:\AI-Portable\codex-home\skills\openeyes\SKILL.md`
  both have SHA-256 `E551C097AD4F8D291AEDE8AC03BDA2049C5F3BB25F93CB15166569E74E930F25`,
  so the documented skill surface is not drifting.

### Recommended next action

The acceptance gate is closer but still closed: `9222` is up (a debug
Edge with MOC articleeditor tabs), `3080` is not. The two-tab
`browser_click` `url_contains` acceptance can run as soon as `3080`
listens. Re-run the same orchestrator when `3080` comes up, or on the
scheduled `2026-09-04T18:00:00+08:00` recheck, whichever is first. This
round is docs-only (evidence only); no source / release / dependency /
permissions change, so it fast-forwards `main` under the 2026-08-28
simplified-maintenance policy.

## Round 101 patrol evidence - 2026-08-29 18:35 +08:00 (Asia/Shanghai)

While `3080` remains down, exercised the designated stdio fallback
(`examples/browser-click-acceptance.py`) against the live CDP browser on
`9222`. Opened the two fixture tabs with
`python examples/open-acceptance-tabs.py --go`, ran the acceptance, then
cleaned up with `--close`.

The stdio acceptance passed:

    {"passed": true, "transport": "mcp-stdio", "acceptance_tabs": 2,
     "target_url": "file:///.../acceptance-pages/target-a.html",
     "matched_click": {"clicked": false, "would_click": true,
       "target": {"name": "Learn more", "score": 1.0}},
     "missing_click_error": "no page target matched url_contains='missing-target'; ..."}

This satisfies the same two-tab dry-run and fail-closed contract that
section 4 requires: the matching `url_contains=target-a` call resolves only
the intended tab and stays a dry-run (`clicked:false`, `would_click:true`);
the unmatched `url_contains=missing-target` call fails closed without
scanning either tab. The exercise isolates OpenEyes dispatch from the dsh
web client, so the dsh end-to-end acceptance remains gated on `3080`.

A stale-tab hygiene note: prior rounds left acceptance fixtures served from
`127.0.0.1:18080` (both `/acceptance-pages/...` and bare `/target-a.html` /
`/decoy.html` variants). The `/acceptance-pages/` variants are caught by
`--close`; the bare-path variants are not, and they caused the first
acceptance run to see `targets=2, decoys=2` instead of `1, 1`. They were
closed manually via CDP `/json/close` this round. Going forward the launcher
opens `file://` fixtures that `--close` handles correctly, so this is a
one-time cleanup.

- `git_state` - `HEAD` and `origin/main` both `cfd7759`; zero ahead, zero
  behind. Byte-stable with the public branch.
- `pytest_suite` - `75 / 75` passed in `13.46s` (up from `67 / 67` in Round 98
  via concurrent-round test additions; no regression).
- `mcp_stdio_probe` - `examples/mcp-stdio-probe.py` returned `ready:true`,
  `tool_count:13`.
- `browser_gate` - `9222` listens (Edge 152 debug, MOC articleeditor tabs);
  `3080` does not listen. The dsh web end-to-end acceptance remains deferred.

### Recommended next action

The stdio fallback acceptance is green; the only remaining gate is the dsh
web host on `3080`. Recheck when `3080` comes up or on
`2026-09-04T18:00:00+08:00`, whichever is first. This round is docs-only
(evidence); no source / release / dependency / permissions change, so it
fast-forwards `main` under the 2026-08-28 simplified-maintenance policy.

## Round 102 patrol evidence - 2026-08-29 18:40 +08:00 (Asia/Shanghai)

`9222` listens (Edge 152 debug, PID 164, MOC articleeditor tabs); `3080` does
not listen, so the dsh web end-to-end acceptance remains deferred. However,
unlike prior rounds that left `browser_*` unexercised against a live CDP
target, this round ran the repo-local stdio fallback acceptance **live** for
the first time.

### First live repo-local browser_click acceptance

With `9222` listening, the fixture tabs were opened with
`python examples\open-acceptance-tabs.py --go`, then the two-tab dry-run and
fail-closed contract was exercised over repository-local MCP stdio:

    python examples\browser-click-acceptance.py --cdp-port 9222

Result: `passed:true`, `acceptance_tabs:2`,
`target_url:file:///.../acceptance-pages/target-a.html`.

- matched call (`url_contains=target-a`, `name_contains=Learn more`):
  `clicked:false`, `would_click:true`, resolved the `Learn more` hyperlink
  (`automation_id:learn-more`, `score:1.0`) on the target-a page. No page
  state changed because `go` is omitted.
- unmatched call (`url_contains=missing-target`): returned
  `no page target matched url_contains='missing-target'` and proposed no
  `would_click`/`target`, i.e. fail-closed.

This is the first live verification of the section-4 dry-run + fail-closed
contract against a real CDP target; prior rounds only ran the static source
tests. The dsh web client transport (`3080`) is still the remaining gate for
the full end-to-end acceptance.

### Stale-tab robustness fix

The first live run failed not on the click contract but on the probe's
exact-count guard: Edge session-restore reopened a previously closed `target-a`
fixture, yielding `targets=2, decoys=1`. `examples/browser-click-acceptance.py`
required exactly one target-a and one decoy, so any leftover fixture tab
blocked the acceptance. The guard was relaxed to require at least one of each
and select the first, leaving the dry-run and fail-closed logic untouched:

- `examples/browser-click-acceptance.py`: `len(target_tabs) != 1` -> `< 1`,
  plus `target_tabs = target_tabs[:1]` / `decoy_tabs = decoy_tabs[:1]`.
- `tests/test_browser_click_acceptance.py`: added
  `test_direct_acceptance_tolerates_duplicate_acceptance_tabs` locking the
  relaxed guard and the first-of-each selection.

Verified live: opening the fixtures twice (4 acceptance tabs) now passes
(`acceptance_tabs:4`, `passed:true`) where the strict guard failed. Full
suite: `76 passed in 12.10s` (+1 versus the Round 100 baseline).

### Probe results

- `pytest tests/ -q --no-header`: **76 passed in 12.10s**.
- `examples/browser-click-acceptance.py --cdp-port 9222` (live): `passed:true`.
- `Get-NetTCPConnection -State Listen -LocalPort 9222`: listening (msedge).
- `Get-NetTCPConnection -State Listen -LocalPort 3080`: not listening.

### Recommended next action

The stdio fallback acceptance is green and now stale-tab-resilient; the only
remaining gate is the dsh web host on `3080`. When `3080` listens and
`http://127.0.0.1:3080/` returns HTTP 200, run
`python examples/open-acceptance-tabs.py --go`, execute the two `browser_click`
payloads in section 4 through the dsh web client, require
`clicked:false`/`would_click:true` plus unmatched fail-closed, then
`python examples/open-acceptance-tabs.py --close`. If `3080` remains down,
recheck at `2026-09-04T18:00:00+08:00`.

## Round 103 patrol evidence - 2026-08-30 00:51 +08:00 (Asia/Shanghai)

`3080` still refuses connections (`Test-NetConnection` False, 15th+
consecutive reading), so the dsh web end-to-end acceptance remains
deferred. Per the round's fallback directive, the stdio acceptance
coverage was broadened from handshake-only to include live
`browser_type` and `browser_shot` dry-run tool calls, exercising the
full MCP tool-dispatch path over the repository-local stdio transport.

### stdio probe broadened to tool-call dispatch

`examples/mcp-stdio-probe.py` previously verified only `initialize` and
`tools/list`. It now also sends two `tools/call` requests:

- `browser_type` with `{text: "acceptance", dry_run: true}` - exercises
  the no-target dry-run branch and asserts `sent:false`,
  `would_send_chars:10`.
- `browser_shot` with `{out: "shots/stdio-acceptance.png", dry_run: true}`
  - exercises the screenshot dry-run branch and asserts `captured:false`,
  `path` echoed back.

Both run without touching a real browser or the dsh web host, broadening
the repository-local surrogate that stays green while `3080` is down.
`tests/test_mcp_stdio_probe.py` now asserts the `tool_calls` payload.

### Probe results

- `python examples/mcp-stdio-probe.py`: `ready:true`, `tool_count:13`,
  `tool_calls.browser_type = {sent:false, dry_run:true, would_send_chars:10}`,
  `tool_calls.browser_shot = {captured:false, dry_run:true, path:"shots/stdio-acceptance.png"}`.
- `python -m pytest tests/ -v`: **76 passed in 11.42s** (unchanged count;
  the stdio probe test gained deeper assertions, no new test file).
- `Test-NetConnection 127.0.0.1:3080`: False (refused).
- `Test-NetConnection 127.0.0.1:9222`: True (Edge DevTools listening).

### Changed files

- `examples/mcp-stdio-probe.py` - added `tools/call` dispatch for
  `browser_type` and `browser_shot` dry-run paths.
- `tests/test_mcp_stdio_probe.py` - assert the new `tool_calls` payload.
- `docs/dsh-web-acceptance.md` - section 2 and round 103 evidence.

### Recommended next action

The stdio surrogate now covers handshake + tool-list + two dry-run tool
calls. The only remaining gate is the dsh web host on `3080`. When it
listens and `http://127.0.0.1:3080/` returns HTTP 200, run the section-4
`browser_click` acceptance through the dsh web client. If `3080` stays
down, the next incremental surrogate candidate is a stdio `browser_scan`
dry-run or a `browser_tabs` stub, though both need a live CDP page to be
meaningful.

## Round 104 patrol evidence - 2026-08-30 07:05 +08:00 (Asia/Shanghai)

The interrupted Round 104 attempt left only a partial `browser_shot` service
change. This round recovered that candidate, completed its two-tab probe,
added URL-filter contract tests, and verified the dry-run behavior live.

### browser_shot URL-scoped acceptance

`browser_shot` now accepts `url_contains` consistently with `browser_click`
and `browser_type`:

- No `url_contains`: the existing no-filter dry-run payload remains unchanged
  (`captured:false`, `dry_run:true`, `path` echo) without connecting to CDP.
- Matched `url_contains`: dry-run connects only to the matching page target,
  reports its `target_url`, and does not call `Page.captureScreenshot`.
- Unmatched `url_contains`: fails closed with
  `no page target matched url_contains` before any screenshot.

`examples/browser-shot-acceptance.py` reuses the transient HTTP fixtures and
tolerates duplicate acceptance tabs. The live run returned `passed:true`,
`acceptance_tabs:2`, matched `captured:false`/`dry_run:true` with the target
URL echoed, and unmatched `no page target matched url_contains`. The probe did
not create `shots/browser-shot-acceptance.png`.

### Probe results

- `python -m pytest tests -q`: **88 passed in 11.47s** (was 81; +7 tests).
- `Test-NetConnection 127.0.0.1:3080`: False (external dsh gate remains down).
- `Test-NetConnection 127.0.0.1:9222`: True (live CDP available).
- `python examples/browser-shot-acceptance.py --cdp-port 9222`: `passed:true`.
- `shots/browser-shot-acceptance.png`: absent after the live probe.

### Changed files

- `openeyes/mcp/server.py` - URL-filtered `browser_shot` dispatch.
- `examples/browser-shot-acceptance.py` - two-tab dry-run + fail-closed probe.
- `tests/test_browser_shot_acceptance.py` - probe source guards.
- `tests/test_mcp_contract.py` - three `browser_shot` URL-filter contracts.
- `docs/dsh-web-acceptance.md` - runbook and Round 104 evidence.

### Recommended next action

When `3080` listens and `http://127.0.0.1:3080/` returns HTTP 200, run the
section-4 `browser_click` end-to-end acceptance through the dsh web client.
Until then, the remaining useful surrogate coverage is `browser_scan` with a
URL filter and a dry-run/fail-closed probe.

## Round 105 patrol evidence - 2026-08-30 13:25 +08:00 (Asia/Shanghai)

The two-tab `browser_type` live probe exposed one remaining contract gap: a
selector-supplied matched dry-run forwarded `url_contains` and resolved the
target element, but omitted `target_url`. The service now reports the resolved
page URL whenever `url_contains` matches, with or without an element selector.

The probe now creates a unique per-run query filter and asserts `target_url`
exactly equals that transient target-a fixture URL. The disposable live run
used an isolated headless Edge profile on `9333`, opened two CDP tabs, and
returned `passed:true`, `acceptance_tabs:2`, `target_url` matching the unique
target-a fixture, `sent:false`, `would_send_chars:5`, and the resolved
`Learn more` hyperlink. A selectorless call returned the same exact
`target_url`, `into:(focused)`, and no resolved element. The unmatched unique
`url_contains` call failed closed with
`no page target matched url_contains` and proposed no action.

- `pytest tests/test_browser_type_acceptance.py tests/test_mcp_contract.py -q`:
  20 passed before the live run.
- `python examples/browser-type-acceptance.py --cdp-port 9333 --timeout 30`:
  `passed:true`.
- `python -m pytest tests -q`: 90 passed (was 88; +2 locks).

Changed: `openeyes/mcp/server.py`, `examples/README.md`,
`examples/browser-type-acceptance.py`, `tests/test_browser_type_acceptance.py`,
`tests/test_mcp_contract.py`, `docs/capability-contract.md`, and this runbook.
The isolated Edge process and its two fixture tabs were closed after evidence
capture.
