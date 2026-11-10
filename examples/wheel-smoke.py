"""Smoke-test the built wheel in a clean virtual environment.

Locate the newest ``dist/*.whl``, install it into a temporary venv, then
verify that ``openeyes`` imports and its bundled decision JSON resources
can be read through ``importlib.resources``. Uses only the standard library.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = r"""
import importlib.resources
import json

import openeyes

resources = [
    item
    for item in importlib.resources.files("openeyes.decision").iterdir()
    if item.is_file() and item.name.endswith(".json")
]
if not resources:
    raise SystemExit("no openeyes.decision JSON resources found")
sample = sorted(resources, key=lambda item: item.name)[0]
content = sample.read_text(encoding="utf-8")
if not content.strip():
    raise SystemExit(f"decision resource is empty: {sample.name}")
print(json.dumps({"resources": len(resources), "sample": sample.name}))
"""


def _run(command: list[str], label: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        print(f"fail: {label} (exit {completed.returncode})", file=sys.stderr)
        if completed.stdout:
            print(completed.stdout, file=sys.stderr, end="")
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
    return completed


def _latest_wheel() -> Path | None:
    wheels = sorted((ROOT / "dist").glob("*.whl"), key=lambda path: path.stat().st_mtime)
    return wheels[-1] if wheels else None


def main() -> int:
    wheel = _latest_wheel()
    if wheel is None:
        print(f"fail: no wheel found under {ROOT / 'dist'}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="openeyes-wheel-smoke-") as temp:
        temp_dir = Path(temp)
        venv_dir = temp_dir / "venv"
        completed = _run([sys.executable, "-m", "venv", str(venv_dir)], "create venv")
        if completed.returncode:
            return 1

        script_dir = "Scripts" if os.name == "nt" else "bin"
        python = venv_dir / script_dir / ("python.exe" if os.name == "nt" else "python")
        completed = _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(wheel),
            ],
            "install wheel",
        )
        if completed.returncode:
            return 1

        completed = _run([str(python), "-I", "-c", PROBE], "probe installed wheel", cwd=temp_dir)
        if completed.returncode:
            return 1

        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            count = int(result["resources"])
            sample = str(result["sample"])
        except (IndexError, KeyError, TypeError, ValueError):
            print("fail: probe returned an invalid summary", file=sys.stderr)
            print(completed.stdout, file=sys.stderr, end="")
            print(completed.stderr, file=sys.stderr, end="")
            return 1

    print(f"ok: {wheel.name} -> decision resources={count}, sample={sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
