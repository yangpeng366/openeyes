"""Fail the build when a source file carries a UTF-8 BOM.

A BOM is invisible in an editor, breaks the Java compiler and other
toolchains, and is easy to reintroduce with a PowerShell `Out-File`.
The rule for this repository is UTF-8 **without** BOM.

Exit codes: 0 clean, 1 at least one file has a BOM, 2 a file is not UTF-8.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".codex", "__pycache__", ".pytest_cache", "node_modules", "shots"}
EXTENSIONS = {".py", ".ps1", ".md", ".yml", ".yaml", ".toml", ".json", ".cfg", ".txt"}
BOM = b"\xef\xbb\xbf"


def main() -> int:
    offenders: list[str] = []
    unreadable: list[str] = []
    checked = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        raw = path.read_bytes()
        checked += 1
        if raw.startswith(BOM):
            offenders.append(str(path.relative_to(ROOT)))
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            unreadable.append(str(path.relative_to(ROOT)))
    print(f"checked {checked} files")
    for name in offenders:
        print(f"BOM   {name}")
    for name in unreadable:
        print(f"UTF8  {name}")
    if offenders:
        print(f"fail: {len(offenders)} file(s) carry a UTF-8 BOM")
        return 1
    if unreadable:
        print(f"fail: {len(unreadable)} file(s) are not valid UTF-8")
        return 2
    print("ok: no BOM, every file decodes as UTF-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())