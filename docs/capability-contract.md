# OpenEyes MCP capability contract

This contract describes the 15 tools exposed by `openeyes.mcp.server` (v0.1.x shipped 13; v0.2.0 adds two read-only tools). It is a compatibility boundary for Codex skills, dsh, and other MCP hosts: changing a tool name, required argument, default execution mode, or effect class is a contract change.

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

## Dry-run contract

All side-effecting tools use `dry_run: true` as their default. Omitting `dry_run` or passing `true` must not create files, launch a process, send keys, or type text. The response includes `dry_run: true` and enough arguments to explain what would happen.

`click` and `grid` return the resolved target/cell with no input event and `clicked: false`.

`browser_click` uses the compatibility switch `go: false`. Omitting `go`, passing `false`, or passing no value must return the resolved target with `clicked: false` and `would_click: true`. Only `go: true` may click.

`browser_type` may connect and scan the DOM during dry-run when a selector is supplied so it can return the resolved target; it must not focus, insert text, or press Enter. When `url_contains` is supplied it must be forwarded to the underlying page selector so a mismatched tab fails closed before any text is sent, even when no element selector is supplied. A matched call also reports the resolved page as `target_url`, including when an element selector is supplied. `browser_shot` dry-run resolves a matching page URL but returns the requested output path without creating it.

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
2. Call `tools/list` and require exactly the 15 names in the matrix above.
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
The two new tools are classified **read** — they observe the accessibility
tree, the DOM, or a local detection file, and never actuate. Existing clients
that enumerate a fixed tool list keep working; a client that asserts an exact
count must move from 13 to 15.

`resolve_elements` and `vision_detect` never actuate. `resolve_elements`
defaults to `port=9222` for the cdp tier; pass `port=0` to skip it.