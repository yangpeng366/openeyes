"""Run the two-tab browser_type acceptance over direct MCP stdio.

This probe broadens the repository-local surrogate beyond ``browser_click`` so
the ``browser_type`` write contract is also accepted while the dsh web-host
gate remains closed. It mirrors ``browser-click-acceptance.py``:
self-contained transient HTTP fixture server, two disposable tabs via the CDP
``/json/new`` endpoint, then a dry-run + fail-closed check over MCP stdio.

A dry-run call must resolve the ``Learn more`` element (``sent:false``,
``would_send_chars`` populated, no text inserted); a selectorless dry-run must
return the exact ``target_url`` without resolving an element; and an unmatched
selectorless ``url_contains`` call must fail closed without proposing
``would_send_chars``/``target``.

The verdicts live in ``_require_type_request``, ``_require_type_dry_run``,
``_require_fail_closed_type`` and ``_select_acceptance_tabs`` so tests can feed
real MCP payloads and assert the values that decide pass/fail instead of
matching sentences in this file.

Two independent surfaces decide the overall verdict, and each gets its own:

* the **page** surface - the MCP ``browser_tabs`` / ``browser_type`` calls;
* the **PowerShell** surface - a real ``pwsh`` command that must reach the
  debug endpoint and print ``200``.

``_combine_verdicts`` makes FAIL beat SKIP and SKIP beat PASS, so a skipped
surface can never be read as a pass. SKIP is reserved for "the leg could not
run" (no listener, no ``pwsh``); a leg that ran and failed is FAIL.

Evidence is written under ``--out``: the receipt is staged and ``os.replace``d
into place only after every check ran, so an interrupted run leaves ``.part`` /
``.tmp`` files but never a half-written PASS. ``_resolve_out_dir`` creates
missing parents, reuses an existing directory without deleting anything, and
refuses to overwrite an earlier receipt unless ``--force`` is passed.
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
TYPE_TEXT = "probe"
NAME_FILTER = "Learn more"
FOCUSED = "(focused)"
NO_PAGE_ERROR = "no page target matched url_contains"
NO_ELEMENT_ERROR = "no element matched the selector"
PROBE_NAME = "browser-type-acceptance"
RECEIPT_NAME = "browser-type-receipt.json"
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


def _type_arguments(
    port: int,
    *,
    text: str,
    url_contains: str,
    name_contains: str | None = None,
    press_enter: bool = False,
) -> dict:
    """Build the exact ``browser_type`` arguments the probe sends.

    ``dry_run`` is never sent, so the tool's own ``true`` default decides the
    contract. ``press_enter`` is only added when the probe means to submit,
    which keeps "no Enter key was dispatched" visible in the request itself.
    """
    arguments: dict = {
        "port": port,
        "url_contains": url_contains,
        "text": text,
    }
    if name_contains is not None:
        arguments["name_contains"] = name_contains
    if press_enter:
        arguments["press_enter"] = True
    return arguments


def _require_exact_text(arguments: dict, expected: str) -> str:
    """The characters handed to ``browser_type`` must be ``expected`` verbatim.

    Guards truncation (``prob``), case changes (``Probe``) and added whitespace
    (``probe ``). All three read as a pass while the probe only compared the
    character count, because ``would_send_chars`` stayed 5 in every case.
    """
    text = arguments.get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"type request carried a non-string text: {text!r}")
    if text != expected:
        raise RuntimeError(
            f"type request text is not verbatim: expected {expected!r}, got {text!r}"
        )
    return text


def _require_type_request(
    arguments: dict,
    *,
    url_contains: str,
    name_contains: str | None,
    expected_text: str,
) -> dict:
    """The request must scope one tab, use the documented selector, submit nothing.

    ``name_contains=None`` is the selectorless branch: it must carry no
    ``hint``/``idx``/``control_type``, otherwise it would type into an element
    instead of the focused control it claims to exercise.
    """
    if arguments.get("url_contains") != url_contains:
        raise RuntimeError(f"type request scoped the wrong tab: {arguments!r}")
    if arguments.get("name_contains") != name_contains:
        raise RuntimeError(f"type request used the wrong selector: {arguments!r}")
    if any(key in arguments for key in ("hint", "idx", "control_type")):
        raise RuntimeError(
            f"type request carried an unexpected selector: {arguments!r}"
        )
    if arguments.get("press_enter"):
        raise RuntimeError(f"type request would dispatch the Enter key: {arguments!r}")
    if "dry_run" in arguments:
        raise RuntimeError(
            f"type request bypassed the dry-run default: {arguments!r}"
        )
    _require_exact_text(arguments, expected_text)
    return arguments


def _require_type_dry_run(
    payload: dict,
    *,
    text: str,
    into: str,
    target_url: str,
    target_name: str | None = None,
) -> dict:
    """A dry-run type must echo the text, resolve the named element, submit nothing.

    Guards five regressions that still read as a pass: the tool inserting text
    (``sent``/``sent_chars``), the readback count disagreeing with the text the
    probe sent, the selector resolving a different element (``into``), an
    Enter key being dispatched, and an error payload being scored as a resolved
    target.
    """
    if not isinstance(payload, dict):
        raise RuntimeError(f"browser_type returned a non-object payload: {payload!r}")
    if "error" in payload:
        raise RuntimeError(
            f"browser_type failed instead of resolving a target: {payload!r}"
        )
    if payload.get("dry_run") is not True or payload.get("sent") is not False:
        raise RuntimeError(f"matched call was not a dry-run: {payload!r}")
    if payload.get("sent_chars") != 0:
        raise RuntimeError(f"dry-run reported inserted characters: {payload!r}")
    if payload.get("would_send_chars") != len(text):
        raise RuntimeError(
            "readback mismatch: would_send_chars="
            f"{payload.get('would_send_chars')!r} for the {len(text)} "
            f"characters {text!r}"
        )
    if payload.get("into") != into:
        raise RuntimeError(f"matched call resolved wrong element: {payload!r}")
    if payload.get("press_enter") is not False:
        raise RuntimeError(f"dry-run would dispatch the Enter key: {payload!r}")
    if payload.get("target_url") != target_url:
        raise RuntimeError(f"matched call reported wrong target URL: {payload!r}")
    target = payload.get("target")
    if target_name is None:
        if target:
            raise RuntimeError(f"selectorless call resolved an element: {payload!r}")
    elif (target or {}).get("name") != target_name:
        raise RuntimeError(f"matched call resolved wrong element: {payload!r}")
    return payload


def _require_fail_closed_type(payload: dict, *, expected: str) -> str:
    """A refused type must name the miss and propose nothing to send."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    error = payload.get("error", "")
    if expected not in error:
        raise RuntimeError(f"missing call did not fail closed: {payload!r}")
    if payload.get("sent") is True or payload.get("sent_chars"):
        raise RuntimeError(f"missing call reported a send outcome: {payload!r}")
    leaked = [
        key
        for key in ("would_send_chars", "target", "target_url", "into")
        if payload.get(key)
    ]
    if leaked:
        raise RuntimeError(f"missing call proposed an action {leaked}: {payload!r}")
    return error


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


def _matched_judgement(accepted: dict) -> dict:
    """The named-selector dry-run, reduced to the fields that decide pass/fail."""
    return {
        "into": accepted["into"],
        "target_name": (accepted.get("target") or {}).get("name"),
        "would_send_chars": accepted["would_send_chars"],
        "sent": accepted["sent"],
        "sent_chars": accepted["sent_chars"],
        "press_enter": accepted["press_enter"],
        "dry_run": accepted["dry_run"],
    }


def _focused_judgement(accepted: dict) -> dict:
    """The selectorless dry-run: no element, exact ``target_url``."""
    return {
        "into": accepted["into"],
        "target_is_null": accepted.get("target") is None,
        "would_send_chars": accepted["would_send_chars"],
        "sent": accepted["sent"],
        "press_enter": accepted["press_enter"],
        "dry_run": accepted["dry_run"],
    }


def _missing_judgement(error: str, *, expected: str = NO_PAGE_ERROR) -> dict:
    """A refusal kept as its documented kind, not as a uuid-bearing message."""
    return {"error_kind": expected, "error_matched": expected in error}


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
            page_port = args.cdp_port
        else:
            fixture_server, fixture_port = _start_fixture_server(EXAMPLES_DIR)
            base_url = f"http://127.0.0.1:{fixture_port}"
        probe_id = uuid.uuid4().hex
        target_filter = f"{TARGET_MARKER}.html?probe={probe_id}"
        missing_filter = f"missing-{probe_id}"
        target_url = f"{base_url}/acceptance-pages/target-a.html?probe={probe_id}"
        decoy_url = f"{base_url}/acceptance-pages/decoy.html?probe={probe_id}"

        # The page leg talks to a listening endpoint even when it is not CDP:
        # "listening but broken" is FAIL, only "nothing listening" is SKIP.
        if page_failure == "cdp-error":
            page_port = fixture_port if fixture_port is not None else _url_port(base_url)
        open_tabs = page_failure != "cdp-error"
        selector_name = (
            f"NoSuchControl-{probe_id}"
            if page_failure == "missing-element"
            else NAME_FILTER
        )

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
                if "browser_tabs" not in tool_names or "browser_type" not in tool_names:
                    raise RuntimeError(
                        "MCP surface does not expose browser_tabs/browser_type"
                    )

                with _check(checks, "tabs"):
                    tabs_detail = _tabs_check(
                        _result_content(call(3, "tools/call", {
                            "name": "browser_tabs",
                            "arguments": {"port": page_port},
                        })),
                        target_filter,
                    )
                checks[-1]["detail"] = tabs_detail
                resolved_target_url = tabs_detail["target_url"]

                matched_arguments = _type_arguments(
                    page_port,
                    text=TYPE_TEXT,
                    url_contains=target_filter,
                    name_contains=selector_name,
                )
                _require_type_request(
                    matched_arguments,
                    url_contains=target_filter,
                    name_contains=selector_name,
                    expected_text=TYPE_TEXT,
                )
                with _check(checks, "matched-dry-run"):
                    matched = _require_type_dry_run(
                        _result_content(call(4, "tools/call", {
                            "name": "browser_type",
                            "arguments": matched_arguments,
                        })),
                        text=TYPE_TEXT,
                        into=selector_name,
                        target_name=selector_name,
                        target_url=resolved_target_url,
                    )
                judgement["matched"] = _matched_judgement(matched)
                checks[-1]["detail"] = judgement["matched"]

                focused_arguments = _type_arguments(
                    page_port,
                    text=TYPE_TEXT,
                    url_contains=target_filter,
                )
                _require_type_request(
                    focused_arguments,
                    url_contains=target_filter,
                    name_contains=None,
                    expected_text=TYPE_TEXT,
                )
                with _check(checks, "selectorless-dry-run"):
                    focused = _require_type_dry_run(
                        _result_content(call(5, "tools/call", {
                            "name": "browser_type",
                            "arguments": focused_arguments,
                        })),
                        text=TYPE_TEXT,
                        into=FOCUSED,
                        target_url=resolved_target_url,
                    )
                judgement["focused"] = _focused_judgement(focused)
                checks[-1]["detail"] = judgement["focused"]

                missing_arguments = _type_arguments(
                    page_port,
                    text=TYPE_TEXT,
                    url_contains=missing_filter,
                )
                with _check(checks, "fail-closed-missing-page"):
                    missing_error = _require_fail_closed_type(
                        _result_content(call(6, "tools/call", {
                            "name": "browser_type",
                            "arguments": missing_arguments,
                        })),
                        expected=NO_PAGE_ERROR,
                    )
                judgement.update(_missing_judgement(missing_error))
                checks[-1]["detail"] = {"error_kind": NO_PAGE_ERROR}
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
        return 130
    finally:
        for target_id in opened_ids:
            _close_tab(page_port, target_id)
        if fixture_server is not None:
            fixture_server.shutdown()
        _stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
