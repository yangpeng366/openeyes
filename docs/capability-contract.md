# OpenEyes MCP capability contract

This contract describes the 19 tools exposed by `openeyes.mcp.server` (v0.1.x shipped 13; v0.2.0 adds two read-only tools, the `app_ensure` lifecycle tool, the two act-then-prove tools `click_by_id` / `act_and_verify`, and the read-only `native_surfaces` discovery tool). It is a compatibility boundary for Codex skills, dsh, and other MCP hosts: changing a tool name, required argument, default execution mode, or effect class is a contract change.

## Safety classes

- **read**: Observes windows, UI trees, browser tabs, or DOM state. It does not click, type, send keys, launch a process, or write a file.
- **write**: Creates a file, launches a browser process, or enters text into the focused UI. These operations expose `dry_run=true` by default and execute only when explicitly disabled.
- **high-risk**: Sends pointer or keyboard actuation that may activate arbitrary application functionality. A click is high-risk even when its selector is precise.

## Tool matrix

| Tool | Surface | Class | Execution switch | Effect |
| --- | --- | --- | --- | --- |
| `list_windows` | Native | read | none | Lists visible top-level windows. |
| `capture_window` | Native | write | `dry_run=true` default; `false` writes | Captures a window or screen to the requested PNG path. |
| `detect_elements` | Native | read | none | Reads the accessibility tree and returns elements. |
| `click` | Native | high-risk | `dry_run=true` default; `false` executes | Resolves a target and optionally clicks it. |
| `grid` | Native | high-risk | `dry_run=true` default; `false` executes | Resolves a grid cell and optionally clicks its center. |
| `hotkey` | Native | high-risk | `dry_run=true` default; `false` sends | Sends the required keyboard chord. |
| `type_text` | Native | write | `dry_run=true` default; `false` types | Types the required literal text into the focused UI. |
| `browser_launch` | Browser | write | `dry_run=true` default; `false` launches | Starts an Edge process with CDP enabled. |
| `browser_tabs` | Browser | read | none | Lists DevTools page targets. |
| `browser_scan` | Browser | read | none | Reads interactive DOM elements and assigns hints. |
| `browser_click` | Browser | high-risk | `go=false` default; `true` executes | Resolves one element and optionally clicks it. |
| `browser_type` | Browser | write | `dry_run=true` default; `false` types | Resolves an optional target via `hint`/`idx`/`name_contains`/`control_type` and types into it. Accepts `url_contains` to scope tab resolution. |
| `browser_shot` | Browser | write | `dry_run=true` default; `false` writes | Captures a viewport PNG to the requested path. |
| `resolve_elements` | Any | read | none | Walks the `uia -> cdp -> vision` chain and returns the first non-empty element set, the tier that answered, and the per-tier trail (ok / count / reason). Never actuates. |
| `vision_detect` | Vision | read | none | Parses screen-parsing detections (OmniParser v2 compatible) into `Element[]`. Reads a file path or inline JSON; no model runs on the host. |
| `app_ensure` | Native | write | `dry_run=true` default; `false` revives | Reports the app state (`running` / `window-gone` / `not-running`), restores a hidden window without stealing focus, or launches it (exe / Store AUMID) and waits until it is **observed** usable. `ok` is true only when usability was observed. |
| `click_by_id` | Native | high-risk | `dry_run=true` default; `false` executes | Clicks a control addressed by its stable `automation_id`, preferring the UIA Invoke pattern (no pixel maths, no cursor movement, no focus theft). The anchor that does not move when the visible label ticks. |
| `act_and_verify` | Native | high-risk | `dry_run=true` default; `false` executes | Acts, then reads the live accessibility tree to prove the expected state (`element_appears` / `element_vanishes` / `label_matches`). `ok` is true only when the state was observed; a delivered action that changed nothing is a miss. |
| `native_surfaces` | Any | read | none | Reports the native ways an application can be driven: a command line it ships, a URL scheme it registers, a registered executable, or a Store AUMID. These are cheaper and more deterministic than driving the window. Lists what it probed, so "nothing found" is never confused with "nothing looked for". |

## Dry-run contract

All side-effecting tools use `dry_run: true` as their default. Omitting `dry_run` or passing `true` must not create files, launch a process, send keys, or type text. The response includes `dry_run: true` and enough arguments to explain what would happen.

`click` and `grid` return the resolved target/cell with no input event and `clicked: false`.

`browser_click` uses the compatibility switch `go: false`. Omitting `go`, passing `false`, or passing no value must return the resolved target with `clicked: false` and `would_click: true`. Only `go: true` may click.

`browser_type` may connect and scan the DOM during dry-run when a selector is supplied so it can return the resolved target; it must not focus, insert text, or press Enter. When `url_contains` is supplied it must be forwarded to the underlying page selector so a mismatched tab fails closed before any text is sent, even when no element selector is supplied. A matched call also reports the resolved page as `target_url`, including when an element selector is supplied. `browser_shot` dry-run resolves a matching page URL but returns the requested output path without creating it.

## CLI delivery path (`eyes click --method`)

The CLI is not part of the MCP tool matrix, but it speaks the same
vocabulary, so the promise is written down here too.

The ladder, cheapest first:

| Rung | Delivery | The user's pointer | The window |
| --- | --- | --- | --- |
| `invoke` | the UIA Invoke pattern | never moves | need not be in the foreground |
| `message` | a window message to the control itself | never moves | need not be in the foreground |
| `pixel` | a synthesized pointer click at the element centre | moved | may come forward |
| `foreground` | take the window to the front, then click | moved | comes forward on purpose |

| `--method` | Rungs it may use |
| --- | --- |
| `auto` (default) | `invoke -> message -> pixel`, first one that works |
| `invoke` / `message` / `pixel` / `foreground` | that rung only: it, or a failure - never a different one |

The rungs that reach for the user's pointer are paid for from
`--budget`: how many times this run may take the foreground away from
you. The default is `0` - never - so a control with no semantic path
fails with a structured error instead of taking your mouse. `--budget N`
allows N such clicks; `--method pixel` asks for one explicitly.

Every `eyes click --json` result states what the click cost:

| Field | Meaning |
| --- | --- |
| `method` | the delivery path that ran (a real click) or the one that was asked for (a dry run). |
| `physical_input` | the automation drove the user's pointer. |
| `cursor_moved` | the pointer was moved by the automation. |
| `focus_stolen` | the focus was pulled onto the target from somewhere else. |
| `error_code` | the structured code, when the action failed. |

The MCP `method` enum is unchanged (`auto` / `invoke` / `pixel`): on
`click_by_id` and `act_and_verify`, `auto` tries Invoke first and falls
back to a pixel click. The MCP surface does not grow a budget knob in
this round - the contract stays as it was.

## Structured error codes

A sentence a human reads is not a contract. These codes are, and an
agent routes on them rather than on prose.

| Code | Meaning |
| --- | --- |
| `BACKGROUND_UNAVAILABLE` | the semantic path could not reach the control, and nothing may take the foreground on the user's behalf. |
| `BUDGET_EXCEEDED` | the disturbance budget for this minute is used up. |
| `NO_SEMANTIC_PATH` | the control offers no operation that can be delivered without the pointer. |
| `LEASE_TIMEOUT` | the physical input lease was not granted within its deadline. |
| `TARGET_CONFLICT` | another agent is already acting on this target. |

## dsh stdio mount

Use [`examples/dsh-openeyes-mcp.yml`](../examples/dsh-openeyes-mcp.yml) as a patch fragment. It starts the server from this repository without relying on a user-specific `eyes-mcp.exe` path:

```yaml
cwd: E:/gitAll/openeyes
command: python
args: ['-m', 'openeyes.mcp.server']
```

The example sets `failOnStartupError: true` so an MCP initialization or tool-list failure surfaces during dsh startup instead of silently removing all `mcp__openeyes__*` tools.

The host bootstrap has two separate prerequisites. `dsh` itself is the profile launcher, while the MCP bridge is an out-of-tree plugin in the selected profile. On the current Windows machine, verify both before the acceptance run:

```powershell
Get-Command dsh
Test-Path 'C:/Users/47037/.dsh/profiles/web/node_modules/@deepseek-ai/dsh-mcp-client'
```

If either check fails, the operator-approved installation sequence is:

```powershell
npm install -g @deepseek-ai/dsh@0.1.1-rc.2
dsh plugin --profile web add @deepseek-ai/dsh-mcp-client@0.1.1-rc.2
dsh --profile web --dump-config
```

Pin the plugin explicitly. As of 2026-08-22, npm reports the plugin `latest`
tag as `0.0.1-rc.1` while `next` and the dsh `0.1.1-rc.2` dependency use
`0.1.1-rc.2`; omitting the version can install the stale plugin.

Before installing anything, run the repository-local read-only preflight:

```powershell
pwsh -NoProfile -File examples\dsh-preflight.ps1
```

Exit code `0` means Python, the OpenEyes MCP import, `dsh`, and the web-profile
MCP client at version `0.1.1-rc.2` are all present. Exit code `2` means one or
more prerequisites are missing; the JSON output lists them in
`missing_prerequisites`. The `next_action` field contains a newline-separated
list of the exact install or repair commands for the missing items, followed by
the command to rerun the preflight. Each line can be executed individually.
Add
`-DumpConfig` only after the preflight passes; it then runs the required
`dsh --profile web --dump-config` check without opening the web UI.

`--dump-config` must be the first launch-mode command after installation: it composes and prints the profile without starting the web UI, exposing plugin or YAML errors before the browser acceptance sequence.

## Acceptance sequence

1. Send MCP `initialize`, then the initialized notification.
2. Call `tools/list` and require exactly the 19 names in the matrix above.
3. Call `browser_click` with a selector and no `go` field; require `clicked: false`, `would_click: true`, and a resolved target. When the browser has multiple page targets, include `url_contains` for the disposable page so resolution is deterministic rather than relying on the first tab.
4. Verify no application state changed during step 3.

`browser_click` retains the compatibility switch `go`; clients should treat `go` and `dry_run` as separate wire-contract fields until a future version unifies them. If `url_contains` is supplied but no page target matches, the browser operation must fail instead of falling back to the first tab; this prevents a stale or mistyped page selector from actuating an unrelated page.

For the dsh web-host procedure, use [docs/dsh-web-acceptance.md](dsh-web-acceptance.md).


## v0.2.0 additions (read-only, additive)

The vision tier and the structured-first degradation chain shipped in v0.2.0.
Two MCP tools expose them to every host (Codex skill, dsh plugin), and the
CLI / Python surfaces remain available.

| Surface | Entry | Notes |
| --- | --- | --- |
| MCP | `resolve_elements` | Walks `uia -> cdp -> vision`; returns elements, `used_backend`, and the per-tier trail. |
| MCP | `vision_detect` | Parses detections (file or inline JSON) into `Element[]`. |
| CLI | `eyes vision --detections <json>` | Converts screen-parsing detections into `Element[]`. Read-only, no model required. |
| CLI | `eyes resolve --window <hwnd> [--port N] [--vision-detections <json>]` | Walks the chain and reports which tier answered, plus per-tier attempt reasons. |
| Python | `openeyes.resolve_elements(...)` | Returns `ResolveResult` (elements + `used_backend` + attempts). |
| Python | `openeyes.backends.vision.elements_from_detections(...)` | OmniParser v2 compatible payload -> `Element[]`. |

Why this is additive rather than a breaking change: no tool was renamed or
removed, no required argument changed, and no default execution mode changed.
`resolve_elements` and `vision_detect` are classified **read** — they
observe the accessibility tree, the DOM, or a local detection file, and
never actuate. `app_ensure` is classified **write**: it can restore a window
and start a process, so it defaults to `dry_run=true` and executes only when
that is explicitly disabled. Existing clients that enumerate a fixed tool
list keep working; a client that asserts an exact count must move from 13 to
16.

`resolve_elements` and `vision_detect` never actuate. `resolve_elements`
defaults to `port=9222` for the cdp tier; pass `port=0` to skip it.

`app_ensure` follows the same rule as the rest of this repository: a
delivered launch is not an effective launch. `ok` is true only when the app
was **observed** to be usable, never merely because a launch command was
issued. In `dry_run` it reports the planned action (`restore` / `launch`)
and performs nothing.

## Python surface additions (additive)

`app_ensure` is the one addition to the MCP matrix in this round; the
entries below are **Python-only** additions that make an existing action
trustworthy. They do not appear in the MCP surface and do not alter it.

| Surface | Entry | Notes |
| --- | --- | --- |
| Python | `openeyes.act_until(act, expect, retries=3, timeout=5.0, interval=0.2, on_retry=None)` | Acts, then polls `expect` until the expected state is observed; retries with the caller's next strategy when it did not take. Returns `{ok, attempts, result, elapsed, error}`. `ok` is True only when the state was observed — a delivered action that changed nothing is a miss. |
| Python | `openeyes.wait_until(condition, timeout=5.0, interval=0.2)` | Polls a condition; a raising condition counts as "not yet". |
| Python | `openeyes.click_element(hwnd, automation_id=..., name_regex=..., control_type=..., method="auto", budget=None)` | Resolves a live UIA wrapper and walks the delivery ladder `invoke -> message -> pixel`. `method` is `auto` / `invoke` / `message` / `pixel` / `foreground`; `dry_run` defaults to True. With a `budget`, the rungs that reach for the pointer are paid for from it. Returns a report with `ok`, `method`, `name`, `automation_id`, `center`, `landed`, `physical_input`, `cursor_moved`, `focus_stolen`, `error` and `error_code`. |
| Python | `openeyes.agent.AgentProfile(...)` / `openeyes.agent.FocusBudget(per_minute=0)` | Who an agent is and what it may take from the user. The budget counts foreground steals in a sliding minute; zero means the agent may not take the foreground at all, and every attempt is counted either way. |
| Python | `openeyes.agent.InteractionLease(root=None, timeout_s=5.0)` | An exclusive claim on the physical input, held across processes. On Windows it is an exclusive `CreateFileW` open of a lock file under the user's SID: Windows refuses a second open and closes the handle when the process dies, so a killed agent cannot lock the desk. `acquire()` returns False on timeout (`error_code` = `LEASE_TIMEOUT`); it is also a context manager. Passing one to `click_element` makes the rungs that reach for the pointer wait their turn. |
| Python | `openeyes.agent.CursorOverlay(agent_id="default", label="", color="")` | The agent's own cursor, drawn over the desktop. It is a picture: it never drives the pointer and never takes the foreground (layered, click-through, non-activating). The colour comes from the identity, so two agents are told apart at a glance. `show(x, y)` / `hide()` / `close()`. `render_cursor()` draws the same shape without a screen. |
| Python | `openeyes.agent.ActionLedger(agent_id="default")` | An append-only record of what the agents did: the delivery path, whether the user's pointer was driven, whether the focus was taken, and whether the expected state was observed. `record(**fields)` stores, `entries()` reads a copy, `summary()` counts. It never raises and never intercepts. |
| Python | `openeyes.supervise(..., ledger=None)` | The supervision loop counts the same way (`physical_inputs`, `focus_thefts`, `foreground_changes`, `methods`, `physical_input_rate`) and writes every round in which it really acted into the ledger. A ledger that raises cannot break the loop: losing a receipt may not cost a renewal. |
| Python | `openeyes.find_elements(elements, automation_id=...)` | Filters on the stable control id — the visible name of a live control (a countdown, a spinner) is not a stable selector. |
| Python | `openeyes.ensure_dpi_awareness()` / `openeyes.current_dpi_awareness()` | Per-monitor-v2 awareness, applied once and never downgraded from what the host process already set. Every reported coordinate is then a real screen pixel. |
| Python | `openeyes.move_to(x, y, settle_ms=60, steps=2)` / `openeyes.cursor_pos()` | Moves the cursor in steps and settles before the press; a single `SetCursorPos` followed by a click can be delivered at the *previous* cursor position. |
| Python | `openeyes.app_state(title_contains=, class_name=, process_name=)` | Tells `running` / `window-gone` / `not-running` apart, with the evidence behind the verdict. A session timeout closes the window; the process may still be alive, and the two cases need different remedies. |
| Python | `openeyes.ensure_app(..., ready=None, timeout=30.0, dry_run=True)` | State check → non-activating restore → relaunch → wait until the app is **observed** usable. `ready=` can demand more than "a window exists". |
| Python | `openeyes.restore_window(hwnd)` | Restores a minimised/hidden window with `SW_SHOWNOACTIVATE`, so the accessibility tree comes back without taking the foreground. |
| Python | `openeyes.launch_app(exe=, aumid=)` | Starts an app; Store (MSIX) apps go through `shell:AppsFolder\<AUMID>`. `launched` means the start was issued, not that the app is usable. |

Why this matters for a host that drives a long-lived loop: a click that was
delivered is not a click that took effect. AnyVPN's keepalive button carries
a live countdown, so "the label changed" proves nothing — only the countdown
jumping back up proves the session was renewed. `act_until` is the reusable
form of that distinction.