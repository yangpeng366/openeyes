---
name: openeyes
description: Use OpenEyes (eyes CLI / openeyes Python package / openeyes-mcp server) to see and operate any desktop UI on Windows/macOS/Linux. Capture screens, enumerate interactive elements via the platform accessibility tree (UIA / AX / AT-SPI), click by coords / text / hint, snap to a grid, drag, type, send hotkeys. Use when driving UWP / Win32 / WinUI / browser apps from Codex where no API or DOM is available. The Windows UIA backend needs no ML — Vision backends (OmniParser / Florence) plug in later for legacy UIs.
---

# OpenEyes Skill

OpenEyes is the AI computer-use primitive layer on this machine. It replaces
hardcoded mouse coordinates with **structured detection**: enumerate
interactive elements, find the one matching the user's intent, click its
center.

## Three-tier fallback

1. **UIA detect** (free, no ML) — `eyes detect --window <hwnd> --name-contains "<text>"`
   Returns bbox center, always click there. Covers ~90% of accessible apps
   (Any VPN, Edge, File Explorer, VS Code, Feishu, Sublime, Word, Excel…).
2. **Hardcoded coords** — only when UIA returns nothing and the target is a
   static, known-position element.
3. **Screenshot + vision model** — when neither UIA nor coords suffice
   (custom canvas, embedded WebView2 contents, legacy non-accessible apps).
   Pluggable via `openeyes-vision-omniparser` / `openeyes-vision-florence`.

## Quick commands

```bash
# 1. find hwnd
eyes windows list --title-contains "飞书"

# 2. capture
eyes capture --window <hwnd> --out shot.png

# 3. detect (dry-run is automatic)
eyes detect --window <hwnd> --pretty

# 4. click by name (dry-run is default; pass --go to actually click)
eyes click --window <hwnd> --name-contains "消息" --go

# 5. click by absolute coords
eyes click --x 100 --y 200 --go

# 6. Vimium-style grid (3x3 default)
eyes grid --window <hwnd> --row 1 --col 2 --go

# 7. hotkey / type
eyes hotkey --combo ctrl+a
eyes type --text "hello"
```

## MCP server

`eyes-mcp` exposes the same 7 primitives as MCP tools:

- `list_windows`
- `capture_window`
- `detect_elements`
- `click` (dry_run=true default)
- `grid`
- `hotkey`
- `type_text`

Start: `eyes-mcp` (stdio transport).

## Tips

- Find hwnd: `eyes windows list --title-contains "飞书"` → grab `hwnd` field.
- Combine `--name-contains` with `--control-type` (e.g. `Button`) to disambiguate.
- Pass `--return-to-origin` after a click to leave the user's cursor where
  it was.
- After clicking a state-changing button (e.g. `Secure my connection`),
  re-run `eyes detect` to verify the new state — UIA tree is the source
  of truth.
- UWP apps: title is on `ApplicationFrameWindow`; controls live inside a
  child `Windows.UI.Core.CoreWindow`. Pass `--restore` to call
  `ShowWindow(SW_RESTORE)` before enumerating (the tree is empty when
  minimized).

## Hard-won facts (this machine)

- Python 3.11.5, Pillow 11.2.1, pywinauto 0.6.9, pywin32 — already installed.
- DPI: multi-monitor 3840x1200. Use window-relative coords; absolute coords
  break when the window moves.
- `pywinauto.rect.left/top/width/height` may be int property or callable —
  use `_call()` wrapper.
- `node.children` in UIA backend is a method — `node.children()`.

## Python API

```python
from openeyes import list_windows, find_window, capture_window
from openeyes import detect_elements, find_elements, click_by_selector

wins = find_window(title_contains="飞书")
if wins:
    hwnd = wins[0].hwnd
    img = capture_window(hwnd)
    img.save("feishu.png")
    elems = detect_elements(hwnd, restore=True)
    msg = find_elements(elems, name_contains="消息")[0]
    target = click_by_selector(hwnd, name_contains="消息", dry_run=True)
    print("would click", target.center, target.name)
```