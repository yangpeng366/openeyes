"""Run the two-tab browser_scan acceptance over direct MCP stdio.

This probe covers the read-only ``browser_scan`` URL-filter contract the same
way ``browser-type-acceptance.py`` covers ``browser_type``: self-contained
transient HTTP fixture server, two disposable tabs via the CDP ``/json/new``
endpoint, then a matched / decoy / fail-closed check sequence over MCP stdio.

A matched ``url_contains`` scan must resolve elements only from the matching
target-a tab; a second scan scoped to the decoy filter must resolve the same
``Learn more`` link on the decoy tab (proving the filter selects tabs, not
element absence); an unmatched ``url_contains`` scan must fail closed with
``no page target matched url_contains`` and return no element list; and a
``name_contains`` that matches nothing must return an empty list rather than
some other element.

The verdicts live in ``_require_exact_scan_names``, ``_require_fail_closed_scan``,
``_require_fail_closed_scan_miss`` and ``_select_acceptance_tabs`` so tests can
feed real MCP payloads and assert the values that decide pass/fail.

Two independent surfaces decide the overall verdict: the MCP **page** surface
and a real **PowerShell** command that must reach the debug endpoint and print
``200``. The rules that combine them - FAIL > SKIP > PASS, the exit-code table,
the judgement digest and the ``--out`` / interruption contract - are shared
with the other three probes and imported from ``examples/_acceptance_verdict.py``
rather than re-implemented here.
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
ACCEPTANCE_MARKER = "acceptance-pages"
TARGET_MARKER = "target-a"
DECOY_MARKER = "decoy"
TARGET_NAME = "Secondary action"
DECOY_NAME = "Learn more"
MISSING_FILTER = "missing-target"
NO_PAGE_ERROR = "no page target matched url_contains"
NO_ELEMENT_ERROR = "no element matched the selector"
PROBE_NAME = "browser-scan-acceptance"
RECEIPT_NAME = "browser-scan-receipt.json"
PAGE_FAILURES = ("missing-page", "missing-element", "cdp-error")

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


# ===== scan contract =====


def _scan_element_names(payload) -> list[str]:
    if not isinstance(payload, list):
        raise RuntimeError(f"browser_scan returned a non-list payload: {payload!r}")
    return [e.get("name") or "" for e in payload]


def _scan_arguments(
    port: int, *, url_contains: str, name_contains: str
) -> dict:
    """The exact ``browser_scan`` arguments the probe sends: one tab, one name."""
    return {
        "port": port,
        "url_contains": url_contains,
        "name_contains": name_contains,
    }


def _require_exact_scan_names(payload, expected: str, *, label: str) -> list[str]:
    """The filtered scan must return exactly ``[expected]``.

    Guards three real regressions: comparing with ``in`` (a decoy-tab element
    leaks through), dropping the empty check (a scan that found nothing is
    accepted), and treating ``name_contains`` as case-sensitive equality
    against a different element.
    """
    names = _scan_element_names(payload)
    if names != [expected]:
        raise RuntimeError(f"{label}: expected [{expected!r}], got {names!r}")
    return names


def _require_fail_closed_scan(payload) -> str:
    """An unmatched ``url_contains`` must name the miss and return no element."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    error = payload.get("error", "")
    if NO_PAGE_ERROR not in error:
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    leaked = [
        key
        for key in ("elements", "target", "target_url", "results", "url")
        if payload.get(key)
    ]
    if leaked:
        raise RuntimeError(
            f"missing call returned a target for {leaked}: {payload!r}"
        )
    return error


def _require_fail_closed_scan_miss(payload, *, name: str) -> str:
    """A ``name_contains`` that matches nothing must return nothing.

    ``browser_scan`` answers ``[]`` here (the tab matched, the element did
    not), so an empty list is the documented refusal. Anything else - one
    element, or a non-list payload - means the filter widened and the probe
    stops calling that a pass.
    """
    if isinstance(payload, dict):
        error = payload.get("error", "")
        if NO_ELEMENT_ERROR in error:
            return error
        raise RuntimeError(
            f"scan for absent name {name!r} did not fail closed: {payload!r}"
        )
    names = _scan_element_names(payload)
    if names:
        raise RuntimeError(
            f"scan for absent name {name!r} returned {len(names)} element(s): {names!r}"
        )
    return ""


def _require_tab_list(payload) -> list[dict]:
    """``browser_tabs`` must answer with a list; a CDP error is a failure."""
    if not isinstance(payload, list):
        detail = payload.get("error") if isinstance(payload, dict) else payload
        raise RuntimeError(f"browser_tabs returned no tab list: {detail!r}")
    return payload


def _select_acceptance_tabs(
    tabs: list[dict], target_filter: str, decoy_filter: str
) -> tuple[list[dict], dict, dict]:
    """Return the acceptance tab list plus exactly one target and one decoy tab.

    Edge session-restore can reopen previously closed fixture tabs, so the
    contract is "at least one of each" and the first match is used. A run that
    silently drops the decoy tab would prove the filter matches nothing.
    """
    acceptance_tabs = [
        tab for tab in tabs if ACCEPTANCE_MARKER in (tab.get("url") or "")
    ]
    target_tabs = [
        tab for tab in acceptance_tabs if target_filter in (tab.get("url") or "")
    ]
    decoy_tabs = [
        tab for tab in acceptance_tabs if decoy_filter in (tab.get("url") or "")
    ]
    if len(target_tabs) < 1 or len(decoy_tabs) < 1:
        raise RuntimeError(
            "expected at least one target-a and one decoy acceptance "
            f"tab; got targets={len(target_tabs)}, "
            f"decoys={len(decoy_tabs)}, tabs={tabs!r}"
        )
    return acceptance_tabs, target_tabs[0], decoy_tabs[0]


def _tabs_check(
    payload, target_filter: str, decoy_filter: str
) -> dict:
    """Validate the tab payload and pick exactly one target and one decoy."""
    tabs = _require_tab_list(payload)
    acceptance_tabs, target_tab, decoy_tab = _select_acceptance_tabs(
        tabs, target_filter, decoy_filter
    )
    return {
        "acceptance_tabs": len(acceptance_tabs),
        "target_id": target_tab.get("id"),
        "target_url": target_tab.get("url"),
        "decoy_url": decoy_tab.get("url"),
    }


# ===== judgement (the values that decide pass/fail) =====


def _names_judgement(names: list[str]) -> dict:
    """A scan's answer reduced to the names and the count that decided it."""
    return {"names": names, "count": len(names)}


def _missing_judgement(error: str) -> dict:
    """A refusal kept as its documented kind, not as its full message."""
    return {"error_kind": NO_PAGE_ERROR, "error_matched": NO_PAGE_ERROR in error}


def _miss_judgement(error: str) -> dict:
    """The absent-element refusal: the tab matched, no element did."""
    return {
        "error_kind": NO_ELEMENT_ERROR if error else "empty-result",
        "refused": True,
        "count": 0,
    }


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
        default="missing-element",
        help="which page-side failure --inject-failure page|both provokes",
    )
    parser.add_argument(
        "--ps-command",
        default=None,
        help="override the PowerShell leg (must exit 0 and print 200 to pass)",
    )
    parser.add_argument("--ps-timeout", type=float, default=30.0)
    parser.add_argument(
        "--force", action="store_true", help="replace an existing receipt in --out"
    )
    args = parser.parse_args(argv)
    _configure_stdout()
    interrupt_state: dict = {"requested": False}
    _install_interrupt_handler(interrupt_state)

    started_at = _utc_now()
    page_failure = _page_failure_of(args)
    try:
        out_dir = _resolve_out_dir(args.out or _default_out_dir(PROBE_NAME))
        _require_free_evidence(out_dir, [RECEIPT_NAME], force=args.force)
    except OutDirError as exc:
        return _out_dir_failure(PROBE_NAME, exc)

    receipt_path = out_dir / RECEIPT_NAME
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
        target_url = f"{base_url}/acceptance-pages/{TARGET_MARKER}.html"
        decoy_url = f"{base_url}/acceptance-pages/{DECOY_MARKER}.html"
        probe_id = uuid.uuid4().hex
        missing_name = (
            f"NoSuchControl-{probe_id}"
            if page_failure == "missing-element"
            else TARGET_NAME
        )

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
                if "browser_tabs" not in tool_names or "browser_scan" not in tool_names:
                    raise RuntimeError(
                        "MCP surface does not expose browser_tabs/browser_scan"
                    )

                with _check(checks, "tabs"):
                    tabs_detail = _tabs_check(
                        _result_content(call(3, "tools/call", {
                            "name": "browser_tabs",
                            "arguments": {"port": page_port},
                        })),
                        TARGET_MARKER,
                        DECOY_MARKER,
                    )
                checks[-1]["detail"] = tabs_detail

                with _check(checks, "matched-scan"):
                    matched_names = _require_exact_scan_names(
                        _result_content(call(4, "tools/call", {
                            "name": "browser_scan",
                            "arguments": _scan_arguments(
                                page_port,
                                url_contains=TARGET_MARKER,
                                name_contains=missing_name,
                            ),
                        })),
                        TARGET_NAME,
                        label="matched scan did not isolate the target-a Secondary action",
                    )
                judgement["matched"] = _names_judgement(matched_names)
                checks[-1]["detail"] = judgement["matched"]

                with _check(checks, "decoy-scan"):
                    decoy_names = _require_exact_scan_names(
                        _result_content(call(5, "tools/call", {
                            "name": "browser_scan",
                            "arguments": _scan_arguments(
                                page_port,
                                url_contains=DECOY_MARKER,
                                name_contains=DECOY_NAME,
                            ),
                        })),
                        DECOY_NAME,
                        label="decoy scan did not isolate the decoy Learn more link",
                    )
                judgement["decoy"] = _names_judgement(decoy_names)
                checks[-1]["detail"] = judgement["decoy"]

                with _check(checks, "fail-closed-missing-page"):
                    missing_error = _require_fail_closed_scan(
                        _result_content(call(6, "tools/call", {
                            "name": "browser_scan",
                            "arguments": _scan_arguments(
                                page_port,
                                url_contains=MISSING_FILTER,
                                name_contains=DECOY_NAME,
                            ),
                        }))
                    )
                judgement["missing_page"] = _missing_judgement(missing_error)
                checks[-1]["detail"] = {"error_kind": NO_PAGE_ERROR}

                with _check(checks, "fail-closed-missing-element"):
                    miss_error = _require_fail_closed_scan_miss(
                        _result_content(call(7, "tools/call", {
                            "name": "browser_scan",
                            "arguments": _scan_arguments(
                                page_port,
                                url_contains=TARGET_MARKER,
                                name_contains=f"Absent-{probe_id}",
                            ),
                        })),
                        name=f"Absent-{probe_id}",
                    )
                judgement["missing_element"] = _miss_judgement(miss_error)
                checks[-1]["detail"] = judgement["missing_element"]
                page = _leg(PASS, checks=checks)
            except PageCheckError as exc:
                page = _leg(FAIL, reason=f"page-{exc.check}", checks=checks,
                            error=str(exc))
            except Exception as exc:
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
            _staged(receipt_path),
            receipt_path.with_name(receipt_path.name + ".tmp"),
        )
        print(json.dumps({
            "probe": PROBE_NAME,
            "verdict": INTERRUPTED,
            "reason": "interrupted-before-receipt",
            "checks": checks,
            "evidence": {"directory": str(out_dir), "receipt": None},
        }, ensure_ascii=False))
        return EXIT_INTERRUPTED
    finally:
        for target_id in opened_ids:
            _close_tab(page_port, target_id)
        if fixture_server is not None:
            fixture_server.shutdown()
        _stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
