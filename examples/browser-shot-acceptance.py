"""Run the two-tab browser_shot dry-run acceptance over direct MCP stdio.

This probe broadens the repository-local surrogate so the ``browser_shot``
write contract also accepts the URL selector while the dsh web-host gate
remains closed. It starts a transient HTTP fixture server, opens target and
decoy tabs through CDP, then performs both checks over MCP stdio.

The matched dry-run call must resolve ``target-a`` and report its URL without
creating a PNG. The unmatched ``url_contains`` call must fail closed with
``no page target matched url_contains`` and must not report ``target_url``.

Neither call may touch the filesystem, so the probe snapshots the ``--out``
file before calling and rejects any new or modified capture. The verdicts live
in ``_require_shot_dry_run``, ``_require_fail_closed_shot``,
``_require_no_new_capture`` and ``_select_acceptance_tabs``; the capture half
(``_ensure_out_dir``, ``_require_png_capture``) is driven by tests with real
PNG bytes so a placeholder path can no longer pass as a screenshot.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
ACCEPTANCE_MARKER = "acceptance-pages"
TARGET_MARKER = "target-a"
SHOT_PATH = "shots/browser-shot-acceptance.png"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"IEND\xaeB`\x82"
NO_PAGE_ERROR = "no page target matched url_contains"


def _request(request_id: int, method: str, params: dict | None = None) -> str:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False)


def _notification(method: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method}, ensure_ascii=False
    )


def _read_stdout(stream, lines: queue.Queue[str]) -> None:
    for line in stream:
        if line.strip():
            lines.put(line)


def _stop_process(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.stderr.read() if process.stderr else ""


def _result_content(result: dict) -> dict:
    content = result["content"]
    if len(content) != 1 or content[0].get("type") != "text":
        raise RuntimeError(f"unexpected tool result content: {content!r}")
    return json.loads(content[0]["text"])


def _shot_arguments(port: int, *, out: str, url_contains: str) -> dict:
    """Build the exact ``browser_shot`` arguments the probe sends.

    ``dry_run`` is never sent, so the tool's own ``true`` default decides the
    contract: a shot is a write because it would create a PNG.
    """
    return {"port": port, "out": out, "url_contains": url_contains}


def _file_state(path: str | Path) -> tuple[int, int] | None:
    """Size and mtime of ``path``, or ``None`` when it does not exist yet."""
    target = Path(path)
    if not target.exists():
        return None
    stat = target.stat()
    return stat.st_size, stat.st_mtime_ns


def _require_no_new_capture(path: str | Path, *, before: tuple[int, int] | None) -> None:
    """A dry-run must leave ``--out`` exactly as it found it.

    Guards the write the whole probe exists to rule out: a PNG appearing, being
    truncated, or being rewritten while the payload still says ``captured:false``.
    """
    after = _file_state(path)
    if after != before:
        raise RuntimeError(
            f"dry-run wrote or touched {Path(path)}: before={before!r}, after={after!r}"
        )


def _ensure_out_dir(out_path: str) -> Path:
    """Mirror ``cdp.screenshot``: create missing ``--out`` parents, return absolute."""
    target = Path(out_path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _require_shot_dry_run(payload: dict, *, out: str, target_url: str) -> dict:
    """A dry-run shot must resolve the target URL and claim no file.

    Guards four regressions that still read as a pass: a real capture
    (``captured``/``dry_run`` flipped), the echoed path drifting from ``--out``,
    the filter resolving the decoy tab, and an error payload that also carries
    ``captured:false`` being scored as a resolved target.
    """
    if not isinstance(payload, dict):
        raise RuntimeError(f"browser_shot returned a non-object payload: {payload!r}")
    if "error" in payload:
        raise RuntimeError(
            f"browser_shot failed instead of resolving a target: {payload!r}"
        )
    if payload.get("captured") is not False or payload.get("dry_run") is not True:
        raise RuntimeError(f"matched call was not a dry-run: {payload!r}")
    if payload.get("path") != out:
        raise RuntimeError(f"matched call echoed the wrong path: {payload!r}")
    if payload.get("target_url") != target_url:
        raise RuntimeError(f"matched call resolved the wrong target: {payload!r}")
    return payload


def _require_fail_closed_shot(payload: dict) -> str:
    """An unmatched ``url_contains`` must name the miss and claim no output."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    error = payload.get("error", "")
    if NO_PAGE_ERROR not in error:
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    if payload.get("captured") is True:
        raise RuntimeError(f"missing call claimed a capture: {payload!r}")
    if payload.get("path") or payload.get("target_url"):
        raise RuntimeError(f"missing call proposed a target: {payload!r}")
    return error


def _read_png(path: str | Path) -> tuple[int, int, int]:
    """Return ``(width, height, byte_count)`` of a complete PNG on disk.

    Reads the real file: the 8-byte signature, the IHDR chunk, the declared
    geometry and the closing IEND chunk. A placeholder path, a JPEG, an empty
    file, a header-only stub and a truncated download all fail here.
    """
    target = Path(path)
    if not target.is_file():
        raise RuntimeError(f"no capture at {target}")
    data = target.read_bytes()
    if not data:
        raise RuntimeError(f"capture at {target} is empty")
    if not data.startswith(PNG_MAGIC):
        raise RuntimeError(
            f"capture at {target} is not a PNG: magic {data[:8]!r}"
        )
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise RuntimeError(
            f"capture at {target} has no IHDR chunk: {len(data)} bytes"
        )
    if not data.endswith(PNG_IEND):
        raise RuntimeError(
            f"capture at {target} is truncated: {len(data)} bytes without IEND"
        )
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width < 1 or height < 1:
        raise RuntimeError(f"capture at {target} declares {width}x{height}")
    return width, height, len(data)


def _require_png_capture(
    payload: dict, *, out: str, region: tuple[int, int]
) -> dict:
    """A real capture must point at its own PNG of exactly the requested region.

    The probe itself stays on the dry-run path; this is the capture verdict the
    tests drive with a synthetic PNG, so ``captured:true`` with a stub path, a
    zero-byte file, a foreign format or the wrong viewport cannot pass.
    """
    if not isinstance(payload, dict):
        raise RuntimeError(f"browser_shot returned a non-object payload: {payload!r}")
    if "error" in payload:
        raise RuntimeError(f"browser_shot failed instead of capturing: {payload!r}")
    if payload.get("captured") is not True or payload.get("dry_run") is not False:
        raise RuntimeError(f"capture did not run: {payload!r}")
    reported = Path(payload.get("path") or "")
    if reported.absolute() != Path(out).absolute():
        raise RuntimeError(
            f"capture landed outside the requested --out: {payload!r}"
        )
    width, height, _size = _read_png(reported)
    if (width, height) != (region[0], region[1]):
        raise RuntimeError(
            f"capture region {width}x{height} does not match the requested "
            f"{region[0]}x{region[1]}"
        )
    return payload


def _select_acceptance_tabs(
    tabs: list[dict], target_filter: str
) -> tuple[list[dict], dict, dict]:
    """Return the acceptance tabs plus exactly one target and one decoy tab."""
    acceptance_tabs = [
        tab for tab in tabs if ACCEPTANCE_MARKER in (tab.get("url") or "")
    ]
    target_tabs = [
        tab for tab in acceptance_tabs if target_filter in (tab.get("url") or "")
    ]
    decoy_tabs = [
        tab for tab in acceptance_tabs if target_filter not in (tab.get("url") or "")
    ]
    if len(target_tabs) < 1 or len(decoy_tabs) < 1:
        raise RuntimeError(
            "expected at least one target-a and one decoy acceptance tab; "
            f"got targets={len(target_tabs)}, decoys={len(decoy_tabs)}, "
            f"tabs={tabs!r}"
        )
    return acceptance_tabs, target_tabs[0], decoy_tabs[0]


def _port_listening(port: int, host: str = "127.0.0.1",
                    timeout: float = 2.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _start_fixture_server(
    directory: Path,
) -> tuple[http.server.ThreadingHTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _open_tab(cdp_port: int, url: str) -> str:
    encoded = urllib.parse.quote(url, safe="")
    req = urllib.request.Request(
        f"http://127.0.0.1:{cdp_port}/json/new?{encoded}",
        method="PUT",
        headers={"Host": "127.0.0.1"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw).get("id", "") if raw.strip() else ""


def _close_tab(cdp_port: int, target_id: str) -> None:
    if not target_id:
        return
    req = urllib.request.Request(
        f"http://127.0.0.1:{cdp_port}/json/close/{target_id}",
        headers={"Host": "127.0.0.1"},
    )
    with contextlib.suppress(Exception):
        urllib.request.urlopen(req, timeout=5).read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    fixture_server = None
    opened_ids: list[str] = []
    process = subprocess.Popen(
        [sys.executable, "-m", "openeyes.mcp.server"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    lines: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(
        target=_read_stdout,
        args=(process.stdout, lines),
        daemon=True,
    )
    reader.start()

    def call(request_id: int, method: str, params: dict | None = None) -> dict:
        assert process.stdin is not None
        process.stdin.write(_request(request_id, method, params) + "\n")
        process.stdin.flush()
        while True:
            message = json.loads(lines.get(timeout=args.timeout))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            if "result" not in message:
                raise RuntimeError(f"{method} returned no result: {message!r}")
            return message["result"]

    try:
        if not _port_listening(args.cdp_port):
            raise RuntimeError(
                f"debug port {args.cdp_port} not listening - "
                f"start Edge with --remote-debugging-port={args.cdp_port}"
            )
        fixture_server, http_port = _start_fixture_server(EXAMPLES_DIR)
        target_url = (
            f"http://127.0.0.1:{http_port}/acceptance-pages/target-a.html"
        )
        decoy_url = (
            f"http://127.0.0.1:{http_port}/acceptance-pages/decoy.html"
        )
        opened_ids.append(_open_tab(args.cdp_port, target_url))
        opened_ids.append(_open_tab(args.cdp_port, decoy_url))
        time.sleep(0.8)

        call(
            1,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "openeyes-browser-shot-acceptance",
                    "version": "1",
                },
            },
        )
        assert process.stdin is not None
        process.stdin.write(_notification("notifications/initialized") + "\n")
        process.stdin.flush()

        tools = call(2, "tools/list")["tools"]
        tool_names = [tool["name"] for tool in tools]
        if "browser_tabs" not in tool_names or "browser_shot" not in tool_names:
            raise RuntimeError(
                "MCP surface does not expose browser_tabs/browser_shot"
            )

        tabs = _result_content(call(3, "tools/call", {
            "name": "browser_tabs",
            "arguments": {"port": args.cdp_port},
        }))
        acceptance_tabs, target_tab, _decoy_tab = _select_acceptance_tabs(
            tabs, TARGET_MARKER
        )

        before = _file_state(SHOT_PATH)
        matched_arguments = _shot_arguments(
            args.cdp_port, out=SHOT_PATH, url_contains=TARGET_MARKER
        )
        matched = _result_content(call(4, "tools/call", {
            "name": "browser_shot",
            "arguments": matched_arguments,
        }))
        _require_shot_dry_run(
            matched, out=SHOT_PATH, target_url=target_tab["url"]
        )

        missing_arguments = _shot_arguments(
            args.cdp_port, out=SHOT_PATH, url_contains="missing-target"
        )
        missing = _result_content(call(5, "tools/call", {
            "name": "browser_shot",
            "arguments": missing_arguments,
        }))
        error = _require_fail_closed_shot(missing)
        _require_no_new_capture(SHOT_PATH, before=before)
    except (AssertionError, KeyError, OSError, queue.Empty, RuntimeError,
            TypeError, ValueError, json.JSONDecodeError) as exc:
        stderr = _stop_process(process)
        print(json.dumps({
            "passed": False,
            "error": str(exc),
            "stderr": stderr[-2000:],
        }, ensure_ascii=False))
        return 2
    finally:
        for target_id in opened_ids:
            _close_tab(args.cdp_port, target_id)
        if fixture_server is not None:
            fixture_server.shutdown()

    _stop_process(process)
    print(json.dumps({
        "passed": True,
        "transport": "mcp-stdio",
        "acceptance_tabs": len(acceptance_tabs),
        "target_url": target_tab["url"],
        "matched_shot": matched,
        "missing_shot_error": error,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())