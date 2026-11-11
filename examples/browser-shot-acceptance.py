"""Run the two-tab browser_shot acceptance over direct MCP stdio.

This probe broadens the repository-local surrogate so the ``browser_shot``
write contract also accepts the URL selector while the dsh web-host gate
remains closed. It starts a transient HTTP fixture server, opens target and
decoy tabs through CDP, then performs the checks over MCP stdio.

The matched dry-run call must resolve ``target-a`` and report its URL without
creating a PNG. The unmatched ``url_contains`` call must fail closed with
``no page target matched url_contains`` and must not report ``target_url``.
Neither dry-run call may touch the filesystem, so the probe snapshots the
target file before calling and rejects any new or modified capture.

The probe then performs one **real** capture into ``--out`` and verifies the
bytes on disk: signature, IHDR, geometry against the measured viewport, and the
closing IEND chunk. That capture is staged as ``<name>.part`` and only renamed
after every check passed, so a killed run leaves no file that reads as
evidence.

The verdicts live in ``_require_shot_dry_run``, ``_require_fail_closed_shot``,
``_require_no_new_capture`` and ``_select_acceptance_tabs``; the capture half
(``_read_png``, ``_require_png_capture``) is driven by tests with real PNG
bytes so a placeholder path can no longer pass as a screenshot.

Two independent surfaces decide the overall verdict: the MCP **page** surface
and a real **PowerShell** command that must reach the debug endpoint and print
``200``. ``_combine_verdicts`` makes FAIL beat SKIP and SKIP beat PASS; SKIP is
reserved for "the leg could not run" (no listener, no ``pwsh``), and a leg that
ran and failed is FAIL. The receipt is written with ``os.replace`` only after
every check ran, so an interruption never leaves a half-written PASS.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
ACCEPTANCE_MARKER = "acceptance-pages"
TARGET_MARKER = "target-a"
SHOT_NAME = "browser-shot.png"
RECEIPT_NAME = "browser-shot-receipt.json"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"IEND\xaeB`\x82"
NO_PAGE_ERROR = "no page target matched url_contains"
PROBE_NAME = "browser-shot-acceptance"
PAGE_FAILURES = ("missing-page", "cdp-error")


sys.path.insert(0, str(Path(__file__).resolve().parent))
from _acceptance_verdict import (  # noqa: E402
    EXIT_CODES,
    EXIT_INTERRUPTED,
    FAIL,
    INTERRUPTED,
    PASS,
    SKIP,
    OutDirError,
    PageCheckError,
    _check,
    _classify_powershell,
    _close_tab,
    _combine_verdicts,
    _configure_stdout,
    _default_out_dir,
    _discard,
    _file_sha256,
    _finalize,
    _injected_ps_command,
    _install_interrupt_handler,
    _judgement_sha256,
    _leg,
    _notification,
    _open_tab,
    _out_dir_failure,
    _port_listening,
    _powershell_command,
    _read_stdout,
    _receipt,
    _request,
    _require_free_evidence,
    _require_not_interrupted,
    _resolve_out_dir,
    _result_content,
    _run_powershell,
    _staged,
    _start_fixture_server,
    _stop_process,
    _url_port,
    _utc_now,
    _verdict_exit_code,
    _write_receipt_atomic,
)

#: The verdict model this probe judges with, named once so the objects are
#: reachable from ``tests/`` (they are the shared module's, not a copy).
#: ``tests/test_acceptance_verdict_shared.py`` compares them by identity.
VERDICT_CONTRACT = {
    "PASS": PASS,
    "FAIL": FAIL,
    "SKIP": SKIP,
    "INTERRUPTED": INTERRUPTED,
    "EXIT_CODES": EXIT_CODES,
    "EXIT_INTERRUPTED": EXIT_INTERRUPTED,
    "OutDirError": OutDirError,
    "PageCheckError": PageCheckError,
    "_check": _check,
    "_classify_powershell": _classify_powershell,
    "_combine_verdicts": _combine_verdicts,
    "_discard": _discard,
    "_finalize": _finalize,
    "_judgement_sha256": _judgement_sha256,
    "_leg": _leg,
    "_receipt": _receipt,
    "_require_free_evidence": _require_free_evidence,
    "_resolve_out_dir": _resolve_out_dir,
    "_staged": _staged,
    "_verdict_exit_code": _verdict_exit_code,
    "_write_receipt_atomic": _write_receipt_atomic,
}


def _shot_arguments(port: int, *, out: str, url_contains: str) -> dict:
    """Build the exact ``browser_shot`` arguments the probe sends.

    ``dry_run`` is never sent, so the tool's own ``true`` default decides the
    contract: a shot is a write because it would create a PNG.
    """
    return {"port": port, "out": out, "url_contains": url_contains}


def _capture_arguments(port: int, *, out: str, url_contains: str) -> dict:
    """The one call that is allowed to write: ``dry_run`` is explicit here."""
    return {
        "port": port,
        "out": out,
        "url_contains": url_contains,
        "dry_run": False,
    }


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

    The dry-run checks stay write-free; this is the capture verdict, driven in
    tests with a synthetic PNG, so ``captured:true`` with a stub path, a
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


def _require_tab_list(payload) -> list[dict]:
    """``browser_tabs`` must answer with a list; a CDP error is a failure."""
    if not isinstance(payload, list):
        detail = payload.get("error") if isinstance(payload, dict) else payload
        raise RuntimeError(f"browser_tabs returned no tab list: {detail!r}")
    return payload


def _tabs_check(payload, target_filter: str) -> dict:
    """Validate the tab payload and pick exactly one target and one decoy."""
    tabs = _require_tab_list(payload)
    acceptance_tabs, target_tab, decoy_tab = _select_acceptance_tabs(
        tabs, target_filter
    )
    return {
        "acceptance_tabs": len(acceptance_tabs),
        "target_id": target_tab.get("id"),
        "target_url": target_tab.get("url"),
        "decoy_url": decoy_tab.get("url"),
    }


def _dry_run_judgement(accepted: dict, *, out: str) -> dict:
    """The matched dry-run, reduced to the fields that decide pass/fail."""
    return {
        "captured": accepted["captured"],
        "dry_run": accepted["dry_run"],
        "path_is_requested": accepted["path"] == out,
    }


def _missing_judgement(error: str) -> dict:
    """A refusal kept as its documented kind, not as its full message."""
    return {"error_kind": NO_PAGE_ERROR, "error_matched": NO_PAGE_ERROR in error}


def _capture_judgement(detail: dict) -> dict:
    """What the bytes on disk actually are, not what the tool claimed."""
    return {
        "width": detail["width"],
        "height": detail["height"],
        "bytes": detail["bytes"],
        "sha256": detail["sha256"],
    }


def _capture_check(payload, *, out: str, region: tuple[int, int]) -> dict:
    """A real capture: read the bytes back and report what is on disk."""
    accepted = _require_png_capture(payload, out=out, region=region)
    width, height, size = _read_png(out)
    return {
        "captured": accepted["captured"],
        "dry_run": accepted["dry_run"],
        "width": width,
        "height": height,
        "bytes": size,
        "sha256": _file_sha256(out),
    }


def _measure_viewport(port: int, url_contains: str) -> tuple[int, int, float]:
    """Read the rendered viewport size in device pixels (read-only).

    ``Page.getLayoutMetrics`` reports ``visualViewport`` in device pixels, which
    is what ``Page.captureScreenshot`` writes. Multiplying CSS ``innerWidth`` by
    the DPR rounds 1 px short here (1639 vs 1640), so the renderer's own numbers
    are used. The capture is compared against this measurement instead of the
    PNG's header, which would make the geometry check circular.
    """
    if str(REPO_ROOT) not in sys.path:
        # Run as ``python examples/...``: the script dir is sys.path[0], so the
        # checkout (which the MCP subprocess gets through ``-m``) is added here.
        sys.path.insert(0, str(REPO_ROOT))
    from openeyes.backends import cdp as cdp_backend

    conn = cdp_backend.connect(port=port, url_contains=url_contains)
    try:
        metrics = conn.call("Page.getLayoutMetrics")
        dpr = float(conn.evaluate("window.devicePixelRatio") or 1.0)
    finally:
        with contextlib.suppress(Exception):
            conn._ws.close()
    device = metrics.get("visualViewport") or {}
    width = int(device.get("clientWidth") or 0)
    height = int(device.get("clientHeight") or 0)
    if width < 1 or height < 1:
        css = metrics.get("cssVisualViewport") or {}
        width = round(float(css.get("clientWidth") or 0) * dpr)
        height = round(float(css.get("clientHeight") or 0) * dpr)
    if width < 1 or height < 1:
        raise RuntimeError(f"viewport measured as {width}x{height}")
    return width, height, dpr


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


def _page_failure_of(args) -> str | None:
    if args.inject_failure not in ("page", "both"):
        return None
    return args.page_failure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--out",
        default=None,
        help="evidence directory (created if missing); default examples/out/<probe>-<utc>",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="serve the fixtures from this base URL instead of a transient server",
    )
    parser.add_argument(
        "--inject-failure",
        choices=("none", "page", "powershell", "both"),
        default="none",
        help="make one or both surfaces fail for real and report the verdict",
    )
    parser.add_argument(
        "--page-failure",
        choices=PAGE_FAILURES,
        default="missing-page",
        help="which page-side failure --inject-failure page|both provokes",
    )
    parser.add_argument(
        "--ps-command",
        default=None,
        help="override the PowerShell leg (must exit 0 and print 200 to pass)",
    )
    parser.add_argument("--ps-timeout", type=float, default=30.0)
    parser.add_argument(
        "--force", action="store_true", help="replace existing evidence in --out"
    )
    args = parser.parse_args(argv)
    _configure_stdout()
    interrupt_state: dict = {"requested": False}
    _install_interrupt_handler(interrupt_state)

    started_at = _utc_now()
    page_failure = _page_failure_of(args)
    try:
        out_dir = _resolve_out_dir(args.out or _default_out_dir(PROBE_NAME))
        _require_free_evidence(
            out_dir, [RECEIPT_NAME, SHOT_NAME], force=args.force
        )
    except OutDirError as exc:
        return _out_dir_failure(PROBE_NAME, exc)

    receipt_path = out_dir / RECEIPT_NAME
    final_png = out_dir / SHOT_NAME
    staged_png = _staged(final_png)
    checks: list[dict] = []
    judgement: dict = {}
    page = _leg(SKIP, reason="page-leg-not-run")
    powershell = _leg(SKIP, reason="powershell-leg-not-run")
    fixture_server = None
    fixture_port: int | None = None
    page_port = args.cdp_port
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
        if args.base_url:
            base_url = args.base_url.rstrip("/")
        else:
            fixture_server, fixture_port = _start_fixture_server(EXAMPLES_DIR)
            base_url = f"http://127.0.0.1:{fixture_port}"
        target_url = f"{base_url}/acceptance-pages/target-a.html"
        decoy_url = f"{base_url}/acceptance-pages/decoy.html"

        # The page leg talks to a listening endpoint even when it is not CDP:
        # "listening but broken" is FAIL, only "nothing listening" is SKIP.
        if page_failure == "cdp-error":
            page_port = fixture_port if fixture_port is not None else _url_port(base_url)
        open_tabs = page_failure != "cdp-error"

        if not _port_listening(page_port):
            page = _leg(SKIP, reason="cdp-port-not-listening", port=page_port)
        else:
            try:
                if open_tabs:
                    if page_failure != "missing-page":
                        opened_ids.append(_open_tab(page_port, target_url))
                    opened_ids.append(_open_tab(page_port, decoy_url))
                    time.sleep(0.8)

                call(
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {
                            "name": PROBE_NAME,
                            "version": "2",
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

                with _check(checks, "tabs"):
                    tabs_detail = _tabs_check(
                        _result_content(call(3, "tools/call", {
                            "name": "browser_tabs",
                            "arguments": {"port": page_port},
                        })),
                        TARGET_MARKER,
                    )
                checks[-1]["detail"] = tabs_detail
                resolved_target_url = tabs_detail["target_url"]
                region = _measure_viewport(page_port, TARGET_MARKER)
                judgement["viewport"] = {"width": region[0], "height": region[1]}

                # Baseline before any probe call: a dry run that writes anyway
                # must be caught, so this snapshot cannot be taken afterwards.
                before = _file_state(final_png)
                matched_arguments = _shot_arguments(
                    page_port, out=str(final_png), url_contains=TARGET_MARKER
                )
                with _check(checks, "dry-run-target"):
                    matched = _require_shot_dry_run(
                        _result_content(call(4, "tools/call", {
                            "name": "browser_shot",
                            "arguments": matched_arguments,
                        })),
                        out=str(final_png),
                        target_url=resolved_target_url,
                    )
                judgement["dry_run"] = _dry_run_judgement(matched, out=str(final_png))
                checks[-1]["detail"] = judgement["dry_run"]

                missing_arguments = _shot_arguments(
                    page_port, out=str(final_png), url_contains="missing-target"
                )
                with _check(checks, "fail-closed-missing-page"):
                    missing_error = _require_fail_closed_shot(
                        _result_content(call(5, "tools/call", {
                            "name": "browser_shot",
                            "arguments": missing_arguments,
                        }))
                    )
                judgement.update(_missing_judgement(missing_error))
                checks[-1]["detail"] = {"error_kind": NO_PAGE_ERROR}

                with _check(checks, "dry-run-wrote-nothing"):
                    _require_no_new_capture(final_png, before=before)

                # The one real write: staged, verified, then renamed.
                with _check(checks, "capture-png"):
                    capture_detail = _capture_check(
                        _result_content(call(6, "tools/call", {
                            "name": "browser_shot",
                            "arguments": _capture_arguments(
                                page_port, out=str(staged_png), url_contains=TARGET_MARKER
                            ),
                        })),
                        out=str(staged_png),
                        region=region,
                    )
                judgement["capture"] = _capture_judgement(capture_detail)
                checks[-1]["detail"] = judgement["capture"]
                _finalize(staged_png, final_png)
                page = _leg(PASS, checks=checks)
            except PageCheckError as exc:
                _discard(staged_png)
                page = _leg(FAIL, reason=f"page-{exc.check}", checks=checks,
                            error=str(exc))
            except Exception as exc:
                _discard(staged_png)
                page = _leg(FAIL, reason="page-unexpected", checks=checks,
                            error=str(exc))

        ps_override = _injected_ps_command(args.inject_failure, args.ps_command)
        ps_command = _powershell_command(args.cdp_port, ps_override)
        powershell = _classify_powershell(
            _run_powershell(ps_command, timeout=args.ps_timeout)
        )
        powershell["command"] = ps_command

        receipt = _receipt(
            probe=PROBE_NAME,
            page=page,
            powershell=powershell,
            judgement=judgement,
            checks=checks,
            injected_failures={
                "page": page_failure,
                "powershell": ps_override
                if args.inject_failure in ("powershell", "both")
                else None,
            },
            run={
                "started_at": started_at,
                "finished_at": _utc_now(),
                "cdp_port": args.cdp_port,
                "page_port": page_port,
                "base_url": base_url,
                "target_url": target_url,
                "decoy_url": decoy_url,
                "python": sys.version.split()[0],
            },
            evidence={
                "directory": str(out_dir),
                "receipt": str(receipt_path),
                "png": str(final_png) if final_png.is_file() else None,
            },
        )
        try:
            _require_not_interrupted(interrupt_state)
            _write_receipt_atomic(receipt_path, receipt)
        except OSError as exc:
            print(json.dumps({
                "probe": PROBE_NAME,
                "verdict": FAIL,
                "reason": "receipt-write",
                "error": str(exc),
                "judgement": judgement,
            }, ensure_ascii=False))
            return EXIT_CODES[FAIL]
        print(json.dumps(receipt, ensure_ascii=False))
        return _verdict_exit_code(receipt["verdict"])
    except KeyboardInterrupt:
        _discard(
            staged_png,
            final_png,
            receipt_path.with_name(receipt_path.name + ".tmp"),
        )
        print(json.dumps({
            "probe": PROBE_NAME,
            "verdict": INTERRUPTED,
            "reason": "interrupted-before-receipt",
            "checks": checks,
            "evidence": {"directory": str(out_dir), "receipt": None, "png": None},
        }, ensure_ascii=False))
        return 130
    finally:
        for target_id in opened_ids:
            _close_tab(page_port, target_id)
        if fixture_server is not None:
            fixture_server.shutdown()
        _stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
