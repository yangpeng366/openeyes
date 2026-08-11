# OpenEyes Examples

Runnable demonstrations. Default behavior is dry-run unless `--go` is passed.

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

Output:
```
[openeyes] using hwnd=123456 dry_run=True
[openeyes] capturing screenshot...
[openeyes] saved shots/feishu_before.png  size=(754, 878)
[openeyes] enumerated 87 elements
[openeyes] match: Button '消息' @ (300,200)  size=80x30
[openeyes] DRY-RUN: would click '消息' @ (300,200)
```

## anyvpn_keepalive.py

Keep Any VPN's free session alive by re-clicking the secure/reset button.

```powershell
python examples\anyvpn_keepalive.py --dry-run
python examples\anyvpn_keepalive.py --go --interval 20
```