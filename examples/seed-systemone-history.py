"""Seed the local XERJ judge with the repo's labelled history.

    python examples\seed-systemone-history.py --index judgements

Writes the NDJSON documents the node ingests and pushes them in with
`xerj index --index <name> --file <ndjson>`. The node's `[decisions] index`
must name the same index. Re-running is safe: the documents are keyed by
their id, so the same seed does not duplicate.

Prints one JSON line; exits 0 on success, 3 on failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openeyes.decision.history import load_seed, to_records  # noqa: E402


def _xerj() -> str | None:
    found = shutil.which("xerj")
    if found:
        return found
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Xerj" / "xerj.exe"
    return str(candidate) if candidate.exists() else None


def _running_node(data_dir: str) -> tuple[str, list[str]] | None:
    """The xerj process serving `data_dir`, as (executable, arguments).

    `xerj index` refuses to write into a data dir another process holds -
    rightly, two writers would replay each other's WAL - so seeding a live
    node means stopping it first and starting it again afterwards.
    """
    script = (
        "$p = Get-CimInstance Win32_Process -Filter \"Name='xerj.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{data_dir}*' }} | Select-Object -First 1; "
        "if ($p) { $p.ExecutablePath; $p.CommandLine }"
    )
    try:
        done = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:  # noqa: BLE001 - probing must never break the run
        return None
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    executable = lines[0]
    command = lines[1] if len(lines) > 1 else executable
    arguments = shlex.split(command, posix=False)[1:]
    return executable, arguments


def _stop_node(process: tuple[str, list[str]], data_dir: str) -> None:
    """Stop the node serving `data_dir` and clear the lock it leaves behind.

    A killed node does not get to remove its own lock file, and a stale lock
    reads as "another writer is here" - so the lock is cleared once no xerj
    process is left for that data dir.
    """
    script = (
        "$p = Get-CimInstance Win32_Process -Filter \"Name='xerj.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{data_dir}*' }} | Select-Object -First 1; "
        "if ($p) { Stop-Process -Id $p.ProcessId; 'stopped ' + $p.ProcessId }"
    )
    subprocess.run(["pwsh", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=30)
    for _ in range(20):
        if _running_node(data_dir) is None:
            break
        time.sleep(0.5)
    lock = Path(data_dir) / "node.lock"
    if lock.exists() and _running_node(data_dir) is None:
        lock.unlink()


def _start_node(process: tuple[str, list[str]]) -> None:
    executable, arguments = process
    log = Path(tempfile.gettempdir()) / "xerj-node.log"
    with open(log, "ab") as handle:
        subprocess.Popen(
            [executable, *arguments],
            stdout=handle,
            stderr=handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="judgements", help="target index name")
    parser.add_argument("--data-dir", default=r"E:\AI-Portable\xerj-data")
    parser.add_argument("--dry-run", action="store_true", help="print the command, ingest nothing")
    parser.add_argument(
        "--keep-node-running",
        action="store_true",
        help="leave a running node alone (the ingest then fails: it holds the data dir)",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="drop the index first, so the history is exactly the seed (measurements stay reproducible)",
    )
    args = parser.parse_args()

    examples = load_seed()
    records = to_records(examples)
    ndjson = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)

    handle, path = tempfile.mkstemp(suffix=".ndjson", prefix="openeyes-history-")
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(ndjson)

    binary = _xerj()
    if binary is None:
        print(json.dumps({"passed": False, "error": "xerj binary not found"}, ensure_ascii=False))
        return 3

    command = [
        binary, "index",
        "--index", args.index,
        "--file", path,
        "--data-dir", args.data_dir,
    ]
    if args.dry_run:
        print(json.dumps({
            "passed": True,
            "dry_run": True,
            "records": len(records),
            "command": command,
        }, ensure_ascii=False))
        return 0

    running = _running_node(args.data_dir)
    restarted = False
    if running is not None and not args.keep_node_running:
        _stop_node(running, args.data_dir)
        restarted = True

    recreated = False
    if args.recreate:
        target = Path(args.data_dir) / args.index
        if target.exists():
            # The index is a derived artefact of the seed, so replacing it is
            # the point of --recreate; anything else in the data dir stays.
            shutil.rmtree(target)
            recreated = True

    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except Exception as exc:  # noqa: BLE001 - the report must name the cause
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 3
    finally:
        os.unlink(path)
        if restarted:
            _start_node(running)

    if done.returncode != 0:
        print(json.dumps({
            "passed": False,
            "returncode": done.returncode,
            "stderr": (done.stderr or "").strip()[-800:],
        }, ensure_ascii=False))
        return 3

    print(json.dumps({
        "passed": True,
        "index": args.index,
        "records": len(records),
        "labels": sorted({example.label for example in examples}),
        "node_restarted": restarted,
        "index_recreated": recreated,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
