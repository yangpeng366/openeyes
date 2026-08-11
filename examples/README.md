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

First showcase — drives the Feishu (Lark) client to verify the full pipeline.

```powershell
# dry-run (default): lists matches, does NOT click
python examples\feishu_first_test.py

# actual click
python examples\feishu_first_test.py --go

# target a specific element
python examples\feishu_first_test.py --target "通讯录" --go
```