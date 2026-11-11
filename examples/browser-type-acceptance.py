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
import contextlib
import functools
import hashlib
import http.server
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
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
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
INTERRUPTED = "INTERRUPTED"
EXIT_CODES = {PASS: 0, FAIL: 2, SKIP: 3}
PAGE_FAILURES = ("missing-page", "missing-element", "cdp-error")
#: Windows STATUS_CONTROL_C_EXIT, signed and unsigned: the console event that
#: a Ctrl+C / graceful ``taskkill`` sends to every process on the console.
CONSOLE_INTERRUPT_CODES = (3221225786, -1073741510)


# ===== verdict core (pure) =====


def _leg(verdict: str, reason: str | None = None, **detail) -> dict:
    """One surface's verdict. ``reason`` names why it is not PASS."""
    leg: dict = {"verdict": verdict}
    if reason is not None:
        leg["reason"] = reason
    leg.update(detail)
    return leg


def _combine_verdicts(*verdicts: str) -> str:
    """FAIL beats SKIP and SKIP beats PASS: a skipped leg is never a pass."""
    unknown = [verdict for verdict in verdicts if verdict not in (PASS, FAIL, SKIP)]
    if unknown:
        raise ValueError(f"unknown verdict(s): {unknown!r}")
    if FAIL in verdicts:
        return FAIL
    if SKIP in verdicts:
        return SKIP
    return PASS


def _verdict_exit_code(verdict: str) -> int:
    try:
        return EXIT_CODES[verdict]
    except KeyError:
        raise ValueError(f"unknown verdict: {verdict!r}") from None


def _canonical_sha256(payload: dict) -> str:
    blob = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _judgement_sha256(
    *,
    probe: str,
    verdict: str,
    page: str,
    powershell: str,
    checks: list[dict],
    judgement: dict,
) -> str:
    """Hash of the decision only - no timestamps, ports, ids or file paths.

    Two consecutive runs must produce the same value; that is what makes
    "the verdict is stable" a checked fact instead of an impression.
    """
    material = {
        "probe": probe,
        "verdict": verdict,
        "page": page,
        "powershell": powershell,
        "checks": [[entry.get("check"), bool(entry.get("ok"))] for entry in checks],
        "judgement": judgement,
    }
    return _canonical_sha256(material)


def _file_sha256(path: str | Path) -> str | None:
    target = Path(path)
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _configure_stdout() -> None:
    """Print the receipt in UTF-8 even when the console code page is not."""
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _install_interrupt_handler(state: dict) -> None:
    """Note Ctrl+C/SIGBREAK so no receipt is written for an interrupted run."""

    def handler(signum, frame):
        state["requested"] = True
        raise KeyboardInterrupt(f"signal {signum}")

    for name in ("SIGINT", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(number, handler)


def _require_not_interrupted(state: dict) -> None:
    if state.get("requested"):
        raise KeyboardInterrupt("interrupted before the receipt")


# ===== --out contract =====


class OutDirError(RuntimeError):
    """``--out`` cannot hold evidence; the probe must not pretend otherwise."""


def _resolve_out_dir(out: str | Path | None) -> Path:
    """Create ``--out`` (and missing parents) and prove it is writable.

    An existing non-empty directory is reused as-is: nothing is deleted and
    nothing is merged, so a stale receipt cannot be read as this run's.
    """
    if not out:
        raise OutDirError("--out directory is required to store the receipt")
    target = Path(out).expanduser()
    if not target.is_absolute():
        target = target.absolute()
    if target.exists() and not target.is_dir():
        raise OutDirError(f"--out is not a directory: {target}")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutDirError(
            f"--out directory cannot be created: {target}: {exc}"
        ) from exc
    probe = target / ".openeyes-out-probe"
    try:
        probe.write_text("probe\n", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise OutDirError(
            f"--out directory is not writable: {target}: {exc}"
        ) from exc
    return target


def _require_free_evidence(
    out_dir: str | Path, names: list[str], *, force: bool = False
) -> None:
    """Refuse to overwrite earlier evidence unless ``--force`` is given."""
    directory = Path(out_dir)
    existing = [str(directory / name) for name in names if (directory / name).exists()]
    if existing and not force:
        raise OutDirError(
            "refusing to overwrite existing evidence "
            f"{existing}; pass --force to replace it"
        )


def _staged(path: str | Path) -> Path:
    """``<name>.part``: visible as in-progress, never mistakable for evidence."""
    target = Path(path)
    return target.with_name(target.name + ".part")


def _finalize(staged: str | Path, final: str | Path) -> Path:
    final_path = Path(final)
    os.replace(Path(staged), final_path)
    return final_path


def _discard(*paths: str | Path) -> None:
    for path in paths:
        if path is None:
            continue
        with contextlib.suppress(OSError):
            Path(path).unlink()


def _write_receipt_atomic(path: str | Path, payload: dict) -> Path:
    """Write ``.tmp`` then ``os.replace``: a kill leaves no receipt at all."""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    return target


def _default_out_dir(probe: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return str(REPO_ROOT / "examples" / "out" / f"{probe}-{stamp}")


# ===== PowerShell leg =====


def _powershell_command(port: int, override: str | None = None) -> str:
    """The PS leg asks the debug endpoint for ``200`` through real PowerShell."""
    if override:
        return override
    return (
        "$r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "
        f"-Uri 'http://127.0.0.1:{port}/json/version'; $r.StatusCode"
    )


def _run_powershell(command: str, *, timeout: float = 30.0) -> dict:
    record: dict = {
        "executable": None,
        "command": command,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "unavailable": None,
    }
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        record["unavailable"] = "pwsh-not-found"
        return record
    record["executable"] = executable
    try:
        completed = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        return record
    except OSError as exc:
        record["unavailable"] = f"pwsh-not-runnable: {exc}"
        return record
    record["returncode"] = completed.returncode
    record["stdout"] = completed.stdout or ""
    record["stderr"] = completed.stderr or ""
    if completed.returncode in CONSOLE_INTERRUPT_CODES:
        # Ctrl+C reached the child but not this interpreter: it is an
        # interruption, not a failing PowerShell command.
        raise KeyboardInterrupt("console control event stopped the PowerShell leg")
    return record


def _classify_powershell(record: dict, *, expect_status: int = 200) -> dict:
    """PASS only when pwsh ran, exited 0 and printed ``expect_status``.

    SKIP is reserved for an environment that cannot run the leg at all; a
    command that ran and failed is FAIL, which is what an injected failing
    command produces.
    """
    detail = {
        "executable": record.get("executable"),
        "returncode": record.get("returncode"),
        "stdout": (record.get("stdout") or "").strip(),
        "stderr": (record.get("stderr") or "").strip()[-2000:],
        "timed_out": bool(record.get("timed_out")),
    }
    if record.get("unavailable"):
        return _leg(SKIP, reason=str(record["unavailable"]), **detail)
    if detail["timed_out"]:
        return _leg(FAIL, reason="powershell-timeout", **detail)
    if detail["returncode"] != 0:
        return _leg(FAIL, reason=f"powershell-exit-{detail['returncode']}", **detail)
    tokens = detail["stdout"].split()
    if str(expect_status) not in tokens:
        return _leg(FAIL, reason="powershell-unexpected-output", **detail)
    return _leg(PASS, **detail)


# ===== page checks =====


class PageCheckError(RuntimeError):
    """A page-side check failed; carries the check name for the verdict."""

    def __init__(self, check: str, message: str) -> None:
        super().__init__(f"{check}: {message}")
        self.check = check


@contextlib.contextmanager
def _check(checks: list[dict], name: str):
    """Record one named page-side check and turn any failure into its verdict."""
    try:
        yield
    except Exception as exc:
        checks.append({"check": name, "ok": False, "error": str(exc)})
        raise PageCheckError(name, str(exc)) from exc
    checks.append({"check": name, "ok": True, "detail": None})


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


def _url_port(url: str) -> int:
    parsed = urllib.parse.urlparse(url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


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
        print(json.dumps({
            "probe": PROBE_NAME,
            "verdict": FAIL,
            "reason": "out-dir",
            "error": str(exc),
        }, ensure_ascii=False))
        return EXIT_CODES[FAIL]

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

        ps_command = _powershell_command(args.cdp_port, args.ps_command)
        powershell = _classify_powershell(
            _run_powershell(ps_command, timeout=args.ps_timeout)
        )
        powershell["command"] = ps_command

        overall = _combine_verdicts(page["verdict"], powershell["verdict"])
        receipt = {
            "probe": PROBE_NAME,
            "verdict": overall,
            "exit_code": _verdict_exit_code(overall),
            "page": page,
            "powershell": powershell,
            "judgement": judgement,
            "judgement_sha256": _judgement_sha256(
                probe=PROBE_NAME,
                verdict=overall,
                page=page["verdict"],
                powershell=powershell["verdict"],
                checks=checks,
                judgement=judgement,
            ),
            "injected_failures": {
                "page": page_failure,
                "powershell": args.ps_command
                if args.inject_failure in ("powershell", "both")
                else None,
            },
            "run": {
                "started_at": started_at,
                "finished_at": _utc_now(),
                "cdp_port": args.cdp_port,
                "page_port": page_port,
                "base_url": base_url,
                "target_url": target_url,
                "decoy_url": decoy_url,
                "python": sys.version.split()[0],
            },
            "evidence": {
                "directory": str(out_dir),
                "receipt": str(receipt_path),
            },
        }
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
        return _verdict_exit_code(overall)
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
