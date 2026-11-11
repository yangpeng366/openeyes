"""The one acceptance-verdict model behind the four browser write probes.

``browser-type-acceptance.py``, ``browser-shot-acceptance.py``,
``browser-scan-acceptance.py`` and ``browser-click-acceptance.py`` judge the
same two independent surfaces and must not disagree about what PASS, FAIL or
SKIP mean. The rules therefore live here once:

* :func:`_combine_verdicts` - **FAIL > SKIP > PASS**; a skipped leg is never a
  pass because "there was nothing to judge" and "it ran and was right" are two
  different facts;
* :func:`_verdict_exit_code` - the process exit code that goes with a verdict
  (PASS ``0``, FAIL ``2``, SKIP ``3``; an interruption exits ``130``);
* :func:`_classify_powershell` - a real ``pwsh`` command that exits non-zero,
  times out or prints no ``200`` is FAIL; only a missing interpreter is SKIP;
* :func:`_injected_ps_command` - ``--inject-failure powershell|both`` supplies
  a command that must fail, so the PowerShell leg's red state stays reachable;
* :func:`_judgement_sha256` - a digest of the decision alone, so two runs can
  be compared: no ports, timestamps, tab ids or paths;
* :func:`_resolve_out_dir` / :func:`_require_free_evidence` - the ``--out``
  contract, including refusing to read a stale receipt as this run's;
* :func:`_staged` / :func:`_finalize` / :func:`_write_receipt_atomic` /
  :func:`_discard` - evidence is staged and renamed into place, so a killed run
  never leaves a half-written PASS;
* :func:`_install_interrupt_handler` - Ctrl+C / SIGBREAK become an interrupted
  run (exit ``130``) instead of a verdict.

:data:`SHARED_NAMES` lists every name the probes must take from here;
``tests/test_acceptance_verdict_shared.py`` asserts that all four probes do,
and that a private copy of the table inside a probe turns the suite red.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
INTERRUPTED = "INTERRUPTED"
#: Exit code table for the three verdicts. Nothing else may define it.
EXIT_CODES = {PASS: 0, FAIL: 2, SKIP: 3}
#: A run that was stopped before it could conclude does not get a verdict code.
EXIT_INTERRUPTED = 130
#: Windows STATUS_CONTROL_C_EXIT, signed and unsigned: the console event that
#: a Ctrl+C / graceful ``taskkill`` sends to every process on the console.
CONSOLE_INTERRUPT_CODES = (3221225786, -1073741510)

#: Every name a probe must import from this module rather than re-declare.
#: Kept next to the definitions so a new probe cannot quietly fork the model.
SHARED_NAMES = (
    "PASS",
    "FAIL",
    "SKIP",
    "INTERRUPTED",
    "EXIT_CODES",
    "EXIT_INTERRUPTED",
    "CONSOLE_INTERRUPT_CODES",
    "_leg",
    "_combine_verdicts",
    "_verdict_exit_code",
    "_canonical_sha256",
    "_judgement_sha256",
    "_file_sha256",
    "_utc_now",
    "_configure_stdout",
    "_install_interrupt_handler",
    "_require_not_interrupted",
    "OutDirError",
    "_resolve_out_dir",
    "_require_free_evidence",
    "_staged",
    "_finalize",
    "_discard",
    "_write_receipt_atomic",
    "_default_out_dir",
    "_receipt",
    "_out_dir_failure",
    "_powershell_command",
    "INJECTED_POWERSHELL_FAILURE",
    "_injected_ps_command",
    "_run_powershell",
    "_classify_powershell",
    "PageCheckError",
    "_check",
    "_request",
    "_notification",
    "_read_stdout",
    "_stop_process",
    "_result_content",
    "_port_listening",
    "_start_fixture_server",
    "_open_tab",
    "_close_tab",
    "_url_port",
)


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


# ===== receipt =====


def _receipt(
    *,
    probe: str,
    page: dict,
    powershell: dict,
    judgement: dict,
    checks: list[dict],
    run: dict,
    evidence: dict,
    injected_failures: dict,
) -> dict:
    """One receipt shape for all four probes: verdict, exit code and digest.

    The two surfaces stay separate inside the receipt and the combination is
    its own field, so a reader can see which leg failed instead of inferring it
    from the total.
    """
    overall = _combine_verdicts(page["verdict"], powershell["verdict"])
    return {
        "probe": probe,
        "verdict": overall,
        "exit_code": _verdict_exit_code(overall),
        "page": page,
        "powershell": powershell,
        "judgement": judgement,
        "judgement_sha256": _judgement_sha256(
            probe=probe,
            verdict=overall,
            page=page["verdict"],
            powershell=powershell["verdict"],
            checks=checks,
            judgement=judgement,
        ),
        "injected_failures": injected_failures,
        "run": run,
        "evidence": evidence,
    }


def _out_dir_failure(probe: str, exc: Exception) -> int:
    """``--out`` cannot hold evidence: say so, exit FAIL, write nothing."""
    print(json.dumps({
        "probe": probe,
        "verdict": FAIL,
        "reason": "out-dir",
        "error": str(exc),
    }, ensure_ascii=False))
    return EXIT_CODES[FAIL]


# ===== PowerShell leg =====


def _powershell_command(port: int, override: str | None = None) -> str:
    """The PS leg asks the debug endpoint for ``200`` through real PowerShell."""
    if override:
        return override
    return (
        "$r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "
        f"-Uri 'http://127.0.0.1:{port}/json/version'; $r.StatusCode"
    )

#: What ``--inject-failure powershell|both`` runs instead of the real probe:
#: a non-zero exit that never prints ``200``, so the leg must classify as FAIL.
INJECTED_POWERSHELL_FAILURE = "Write-Output 'injected-powershell-failure'; exit 1"


def _injected_ps_command(inject_failure: str | None, override: str | None) -> str | None:
    """Resolve the PowerShell leg's command, honouring ``--inject-failure``.

    An explicit ``--ps-command`` always wins: the operator asked for that exact
    command and the receipt must show it. Otherwise an injection that names the
    PowerShell leg supplies :data:`INJECTED_POWERSHELL_FAILURE`, so the failure
    surface is reachable without hand-writing a command. Returning ``None``
    means "run the real check".
    """
    if override:
        return override
    if inject_failure in ("powershell", "both"):
        return INJECTED_POWERSHELL_FAILURE
    return None


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


# ===== MCP stdio plumbing =====


def _request(request_id: int, method: str, params: dict | None = None) -> str:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return json.dumps(message, ensure_ascii=False)


def _notification(method: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method}, ensure_ascii=False
    )


def _read_stdout(stream, lines) -> None:
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


# ===== fixture tabs =====


def _port_listening(port: int, host: str = "127.0.0.1",
                    timeout: float = 2.0) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _start_fixture_server(directory: Path):
    import functools
    import http.server
    import threading

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _open_tab(cdp_port: int, url: str) -> str:
    import urllib.parse
    import urllib.request

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
    import urllib.request

    if not target_id:
        return
    req = urllib.request.Request(
        f"http://127.0.0.1:{cdp_port}/json/close/{target_id}",
        headers={"Host": "127.0.0.1"},
    )
    with contextlib.suppress(Exception):
        urllib.request.urlopen(req, timeout=5).read()


def _url_port(url: str) -> int:
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80
