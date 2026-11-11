"""Run the two-tab browser_click acceptance over direct MCP stdio.

This probe intentionally bypasses the dsh web client so repository-local MCP
dispatch can be accepted independently while the web-host gate remains closed.

It is self-contained: it starts a transient local HTTP server that serves the
``examples/acceptance-pages/`` fixtures, opens the two disposable tabs via the
CDP ``/json/new`` endpoint, runs the dry-run acceptance, then closes the tabs
and stops the server. Edge discards ``file://`` tabs created through CDP, so
the fixtures are served over HTTP whose URL path retains the
``acceptance-pages`` marker required by the tab filter.

The dry-run verdict lives in ``_require_dry_run_click``; the fail-closed
verdicts live in ``_require_fail_closed_click`` and
``_require_fail_closed_click_miss``. A dry-run payload can look perfect while
the reported centre is covered by an overlay, so the probe also reads the page
through CDP and asks what sits at that centre
(``_require_hittable`` / ``_hit_test``): a link that is present, enabled and
measured but not the topmost element is "present but not clickable", which is
FAIL and not PASS.

Two independent surfaces decide the overall verdict: the MCP **page** surface
and a real **PowerShell** command that must reach the debug endpoint and print
``200``. The rules that combine them - FAIL > SKIP > PASS, the exit-code table,
the judgement digest and the ``--out`` / interruption contract - are shared
with the other three probes and imported from ``examples/_acceptance_verdict.py``
rather than re-implemented here.
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
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"
ACCEPTANCE_MARKER = "acceptance-pages"
TARGET_MARKER = "target-a"
UNHITTABLE_MARKER = "unhittable"
NAME_FILTER = "Learn more"
MISSING_FILTER = "missing-target"
NO_PAGE_ERROR = "no page target matched url_contains"
NO_ELEMENT_ERROR = "no element matched the selector"
PROBE_NAME = "browser-click-acceptance"
RECEIPT_NAME = "browser-click-receipt.json"
PAGE_FAILURES = ("missing-page", "missing-element", "unhittable", "cdp-error")

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


# ===== click contract =====


def _click_arguments(
    port: int, *, url_contains: str, name_contains: str
) -> dict:
    """The exact ``browser_click`` arguments the probe sends.

    ``go`` is never sent: the tool's own ``False`` default is what makes this a
    dry run, so "nothing was clicked" is visible in the request itself.
    """
    return {
        "port": port,
        "url_contains": url_contains,
        "name_contains": name_contains,
    }


def _require_dry_run_click(payload: dict, expected_name: str) -> dict:
    """A dry-run click must resolve ``expected_name`` and report its centre.

    Guards five real regressions: ``clicked`` becoming truthy (a live click
    got through), ``would_click`` being falsy (the element was not resolved),
    the selector resolving a different element, ``center`` being a placeholder
    instead of the resolved target's own centre, and a resolved element that is
    disabled/hidden while the payload still reads as clickable.
    """
    if not isinstance(payload, dict):
        raise RuntimeError(f"browser_click returned a non-object payload: {payload!r}")
    if payload.get("clicked") is not False:
        raise RuntimeError(f"matched call was not a dry-run: {payload!r}")
    if payload.get("would_click") is not True:
        raise RuntimeError(f"matched call was not a dry-run: {payload!r}")
    target = payload.get("target") or {}
    if target.get("name") != expected_name:
        raise RuntimeError(f"matched call resolved wrong element: {payload!r}")
    center = payload.get("center") or {}
    if center != (target.get("center") or {}):
        raise RuntimeError(
            f"matched call reported a centre that is not the target's: {payload!r}"
        )
    bbox = target.get("bbox") or {}
    try:
        left = float(bbox["x"])
        top = float(bbox["y"])
        right = left + float(bbox["w"])
        bottom = top + float(bbox["h"])
        cx = float(center["x"])
        cy = float(center["y"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            f"matched call reported no measurable centre: {payload!r}"
        ) from None
    if not (left <= cx <= right and top <= cy <= bottom):
        raise RuntimeError(
            f"matched call aimed outside the target box: {payload!r}"
        )
    state = target.get("state") or {}
    if state.get("enabled") is False or state.get("visible") is False:
        raise RuntimeError(
            f"matched call resolved a target that cannot be clicked: {payload!r}"
        )
    return payload


def _require_fail_closed_click(payload: dict) -> str:
    """An unmatched ``url_contains`` must name the miss and propose nothing."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    error = payload.get("error", "")
    if NO_PAGE_ERROR not in error:
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    if "would_click" in payload or "clicked" in payload or payload.get("target"):
        raise RuntimeError(f"missing call proposed an action: {payload!r}")
    return error


def _require_fail_closed_click_miss(payload: dict, *, name: str) -> str:
    """A ``name_contains`` that matches nothing must be refused, not guessed.

    The tool must not fall back to the first element on the page, so a payload
    that carries ``would_click``/``target`` here is a failure even though an
    ``error`` key is present.
    """
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"click for absent name {name!r} did not fail closed: {payload!r}"
        )
    error = payload.get("error", "")
    if NO_ELEMENT_ERROR not in error:
        raise RuntimeError(
            f"click for absent name {name!r} did not fail closed: {payload!r}"
        )
    leaked = [
        key
        for key in ("would_click", "clicked", "target", "center")
        if payload.get(key)
    ]
    if leaked:
        raise RuntimeError(
            f"click for absent name {name!r} proposed {leaked}: {payload!r}"
        )
    return error


def _require_hittable(
    report: dict, *, center: dict, selector: str
) -> dict:
    """The element under the reported centre must be the resolved target.

    This is what separates "resolved" from "clickable": a link covered by an
    overlay is still measured, still enabled, and still the element the dry run
    reports - while a real click at that centre lands on the overlay.
    """
    if not isinstance(report, dict):
        raise RuntimeError(f"hit test returned no measurement: {report!r}")
    if report.get("matched") is not True:
        raise RuntimeError(
            f"the reported centre {center} is covered by {report.get('hit')!r}, "
            f"not {selector!r}"
        )
    return report


def _hit_test(port: int, *, url_contains: str, center: dict, selector: str) -> dict:
    """Ask the page what sits at the reported centre (read-only CDP evaluate).

    ``browser_click`` reports document coordinates (the CDP DOM probe adds
    ``scrollX``/``scrollY``), while ``elementFromPoint`` takes viewport ones, so
    the scroll offset is subtracted here - no origin is estimated.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from openeyes.backends import cdp as cdp_backend

    js = (
        "(function(){"
        f"var cx={int(center['x'])}, cy={int(center['y'])};"
        "var sx=window.scrollX||0, sy=window.scrollY||0;"
        "var el=document.elementFromPoint(cx-sx, cy-sy);"
        f"var want=document.querySelector({json.dumps(selector)});"
        "return {hit: el ? el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') : null,"
        f" wanted: {json.dumps(selector)},"
        " matched: !!el && !!want && (el === want || want.contains(el))};"
        "})()"
    )
    conn = cdp_backend.connect(port=port, url_contains=url_contains)
    try:
        return conn.evaluate(js)
    finally:
        with contextlib.suppress(Exception):
            conn._ws.close()


def _require_tab_list(payload) -> list[dict]:
    """``browser_tabs`` must answer with a list; a CDP error is a failure."""
    if not isinstance(payload, list):
        detail = payload.get("error") if isinstance(payload, dict) else payload
        raise RuntimeError(f"browser_tabs returned no tab list: {detail!r}")
    return payload


def _select_acceptance_tabs(
    tabs: list[dict], target_filter: str
) -> tuple[list[dict], dict, dict]:
    """Return the acceptance tabs plus exactly one target and one decoy tab.

    Edge session-restore can reopen previously closed fixture tabs, so the
    contract is "at least one of each" and the first match is used. The decoy
    half is required: without it the ``url_contains`` filter is never proven to
    discriminate, because every tab would satisfy it.
    """
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
            "expected at least one target and one decoy acceptance tab; "
            f"got targets={len(target_tabs)}, decoys={len(decoy_tabs)}, "
            f"tabs={tabs!r}"
        )
    return acceptance_tabs, target_tabs[0], decoy_tabs[0]


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


# ===== judgement (the values that decide pass/fail) =====


def _dry_run_judgement(accepted: dict) -> dict:
    """The dry-run, reduced to the fields that decide pass/fail."""
    target = accepted.get("target") or {}
    return {
        "clicked": accepted["clicked"],
        "would_click": accepted["would_click"],
        "name": target.get("name"),
        "enabled": (target.get("state") or {}).get("enabled"),
        "visible": (target.get("state") or {}).get("visible"),
        "center_is_target": accepted["center"] == (target.get("center") or {}),
    }


def _hittable_judgement(report: dict) -> dict:
    """What the page answered under the reported centre."""
    return {
        "hit": report.get("hit"),
        "wanted": report.get("wanted"),
        "matched": report.get("matched"),
    }


def _missing_judgement(error: str) -> dict:
    """A refusal kept as its documented kind, not as its full message."""
    return {"error_kind": NO_PAGE_ERROR, "error_matched": NO_PAGE_ERROR in error}


def _miss_judgement(error: str) -> dict:
    """The absent-element refusal: the tab matched, no element did."""
    return {"error_kind": NO_ELEMENT_ERROR, "error_matched": NO_ELEMENT_ERROR in error}


def _page_failure_of(args) -> str | None:
    if args.inject_failure not in ("page", "both"):
        return None
    return args.page_failure


def _dedupe(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


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
        decoy_url = f"{base_url}/acceptance-pages/decoy.html"
        probe_id = uuid.uuid4().hex
        # The unhittable fixture keeps the same contract one step further: the
        # link is there and measured, but an overlay owns its centre.
        click_filter = (
            UNHITTABLE_MARKER if page_failure == "unhittable" else TARGET_MARKER
        )
        target_url = f"{base_url}/acceptance-pages/{click_filter}.html"
        selector_name = (
            f"NoSuchControl-{probe_id}"
            if page_failure == "missing-element"
            else NAME_FILTER
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
                if "browser_tabs" not in tool_names or "browser_click" not in tool_names:
                    raise RuntimeError(
                        "MCP surface does not expose browser_tabs/browser_click"
                    )

                with _check(checks, "tabs"):
                    tabs_detail = _tabs_check(
                        _result_content(call(3, "tools/call", {
                            "name": "browser_tabs",
                            "arguments": {"port": page_port},
                        })),
                        click_filter,
                    )
                checks[-1]["detail"] = tabs_detail

                with _check(checks, "dry-run-target"):
                    matched = _require_dry_run_click(
                        _result_content(call(4, "tools/call", {
                            "name": "browser_click",
                            "arguments": _click_arguments(
                                page_port,
                                url_contains=click_filter,
                                name_contains=selector_name,
                            ),
                        })),
                        selector_name,
                    )
                judgement["dry_run"] = _dry_run_judgement(matched)
                checks[-1]["detail"] = judgement["dry_run"]

                target = matched.get("target") or {}
                selector = _dedupe([
                    f"#{target['automation_id']}" if target.get("automation_id") else "",
                    "a[href]",
                ])[0]
                with _check(checks, "target-hittable"):
                    hit_report = _require_hittable(
                        _hit_test(
                            page_port,
                            url_contains=click_filter,
                            center=matched["center"],
                            selector=selector,
                        ),
                        center=matched["center"],
                        selector=selector,
                    )
                judgement["hittable"] = _hittable_judgement(hit_report)
                checks[-1]["detail"] = judgement["hittable"]

                with _check(checks, "fail-closed-missing-page"):
                    missing_error = _require_fail_closed_click(
                        _result_content(call(5, "tools/call", {
                            "name": "browser_click",
                            "arguments": _click_arguments(
                                page_port,
                                url_contains=MISSING_FILTER,
                                name_contains=NAME_FILTER,
                            ),
                        }))
                    )
                judgement["missing_page"] = _missing_judgement(missing_error)
                checks[-1]["detail"] = {"error_kind": NO_PAGE_ERROR}

                with _check(checks, "fail-closed-missing-element"):
                    miss_error = _require_fail_closed_click_miss(
                        _result_content(call(6, "tools/call", {
                            "name": "browser_click",
                            "arguments": _click_arguments(
                                page_port,
                                url_contains=click_filter,
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
