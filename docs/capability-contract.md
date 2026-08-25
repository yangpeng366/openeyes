# OpenEyes MCP capability contract

This contract describes the 13 tools exposed by `openeyes.mcp.server` in v0.1.x. It is a compatibility boundary for Codex skills, dsh, and other MCP hosts: changing a tool name, required argument, default execution mode, or effect class is a contract change.

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

## Dry-run contract

All side-effecting tools use `dry_run: true` as their default. Omitting `dry_run` or passing `true` must not create files, launch a process, send keys, or type text. The response includes `dry_run: true` and enough arguments to explain what would happen.

`click` and `grid` return the resolved target/cell with no input event and `clicked: false`.

`browser_click` uses the compatibility switch `go: false`. Omitting `go`, passing `false`, or passing no value must return the resolved target with `clicked: false` and `would_click: true`. Only `go: true` may click.

`browser_type` may connect and scan the DOM during dry-run when a selector is supplied so it can return the resolved target; it must not focus, insert text, or press Enter. When `url_contains` is supplied it must be forwarded to the underlying page selector so a mismatched tab fails closed before any text is sent. File captures return the requested output path without creating it.

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
2. Call `tools/list` and require exactly the 13 names in the matrix above.
3. Call `browser_click` with a selector and no `go` field; require `clicked: false`, `would_click: true`, and a resolved target. When the browser has multiple page targets, include `url_contains` for the disposable page so resolution is deterministic rather than relying on the first tab.
4. Verify no application state changed during step 3.
If `url_contains` is supplied but no page target matches, the browser operation
must fail instead of falling back to the first tab. This prevents a stale or
mistyped page selector from actuating an unrelated page.

`browser_click` retains the compatibility switch `go`; clients should treat `go` and `dry_run` as separate wire-contract fields until a future version unifies them.

For the dsh web-host procedure, use [docs/dsh-web-acceptance.md](dsh-web-acceptance.md).

