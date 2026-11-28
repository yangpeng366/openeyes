"""Browser task bench: the fixed menu against the code-first escape hatch.

The question is narrow: **for the same browser task, what does each route cost
the agent?**  Three routes run the same task set:

* ``verbs``  - the fixed menu: ``browser scan`` -> ``browser click
  --name-contains`` -> ``browser type``.  One call per action, no loops.
* ``code``   - the code-first escape hatch: one ``browser exec --code`` whose
  JavaScript walks the task's targets in a loop.
* ``control`` - a do-nothing route.  It must fail every task; a control that
  passes means the bench is broken, not that the task was easy.

Nothing here judges code quality or model quality.  The code each route runs is
precomputed from the task's own fields, so the comparison is "one call that
loops" against "N calls that cannot loop".

Contract and vocabulary
-----------------------
* ``docs/browser-task-bench-spec.md`` is the contract; this file implements it
  and does not restate it.
* The verdict vocabulary (PASS / FAIL / SKIP) and the exit-code table come
  from :mod:`openeyes.core.verdict`.  The receipt helpers (``_resolve_out_dir``,
  ``_write_receipt_atomic``, ``_start_fixture_server``, ``_open_tab``,
  ``_stop_process``) come from ``examples/_acceptance_verdict.py``.  This file
  defines no second table, no second vocabulary and no second exit-code map.
* The task set comes from ``benchmarks/_browser_task_suite.py`` (and
  ``_browser_task_suite_extra.py``), which the lead writes.  A missing suite is
  a clear error - the bench loads the suite, it never invents one.

Honesty rules (the spec's, made mechanical here)
------------------------------------------------
1. The verdict comes from an **independent judgement call**: one
   ``browser exec --code <expect_js> --go`` that reads the page *after* the
   route ran.  A route cannot report success - the route's return value
   carries no verdict field at all, so there is nothing for it to lie with.
2. A ``control`` run that passes any task fails the bench (exit 2) and the
   receipt names the offending task.
3. Every page must start "not yet done".  The bench reads the expectation
   *before* the route runs; a page that already satisfies it is a bench bug,
   so the bench verdict becomes FAIL and the task is named.
4. A route that cannot express a task is SKIP with a reason - never FAIL
   ("I cannot") and never PASS.

Call accounting
---------------
``calls`` counts only the **agent-facing CLI invocations of the route** - the
commands an agent would have typed - and ``agent_output_bytes`` counts the
stdout bytes it would have had to read.  That is a context-cost proxy, **not**
a token count; the receipt says so in ``metric_notes``.

The harness's own calls live in their own buckets and never inflate ``calls``:

* ``setup_calls``    - per run: open the fixture tab (and wait for the page to
  be ready) + read the expectation once to prove the page starts not-done.
* ``judgement_calls`` - per run: read the expectation after the route ran.

The readiness wait is CDP plumbing inside the "open the tab" step: it is not a
CLI invocation and not agent-facing, so it is not a call in any bucket.

The route's capability vocabulary
---------------------------------
``requires`` on a task names capability tags.  The verbs route has exactly two
write verbs, so it supports ``click`` and ``type`` and nothing else; the code
route runs the page's own JavaScript, so it also supports ``read_text``.  Any
other tag is unknown to this bench and both routes SKIP it with a reason
rather than pretend.

Two small deviations from the spec's prose, both deliberate
----------------------------------------------------------
* The spec says the verbs route reads ``browser scan --json``.  The ``scan``
  subcommand has no ``--json`` switch - it always prints JSON - and passing
  ``--json`` there is a usage error (exit 2).  The bench calls ``browser scan``
  and parses its JSON.
* ``setup_calls`` is 2, not 1: the pre-run expectation read is a harness call
  and every harness call has to land in a bucket.

Run it:

    python benchmarks/bench_browser_task.py --json
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = REPO_ROOT / "benchmarks"
EXAMPLES_DIR = REPO_ROOT / "examples"
PAGES_DIR = EXAMPLES_DIR / "bench_pages"

for _entry in (str(REPO_ROOT), str(EXAMPLES_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from _acceptance_verdict import (  # noqa: E402
    OutDirError,
    _close_tab,
    _configure_stdout,
    _install_interrupt_handler,
    _open_tab,
    _out_dir_failure,
    _port_listening,
    _require_free_evidence,
    _require_not_interrupted,
    _resolve_out_dir,
    _start_fixture_server,
    _stop_process,
    _write_receipt_atomic,
)

from openeyes.core.verdict import EXIT_CODES, EXIT_INTERRUPTED, FAIL, PASS, SKIP  # noqa: E402

#: One suite module per suite name.  The modules are written by the lead.
SUITE_MODULES = {
    "core": "_browser_task_suite",
    "extra": "_browser_task_suite_extra",
}

#: The routes this bench knows, in the order the receipt lists them.
STRATEGIES = ("verbs", "code", "control")

#: What each route can express, as capability tags.
VERBS_CAPABILITIES = frozenset({"click", "type"})
CODE_CAPABILITIES = frozenset({"click", "type", "read_text"})

#: Control types some backends report for a text-entry control.
EDITABLE_CONTROL_TYPES = frozenset({"Edit", "TextInput", "SearchBox", "TextArea"})

#: ``<input>`` types that take no text, so clicking - not typing - is the verb.
NON_TEXT_INPUT_TYPES = frozenset({
    "button", "checkbox", "color", "file", "hidden", "image", "radio",
    "range", "reset", "submit",
})

#: The receipt file inside ``--out``.
RECEIPT_NAME = "browser-task-bench-receipt.json"

#: One CLI invocation may not take longer than this.
CLI_TIMEOUT_S = 120.0

#: How long the page gets to settle between the route and the judgement.
SETTLE_S = 0.25

#: The notes the receipt carries, word for word the spec's wording.
METRIC_NOTES = {
    "calls": "agent-facing CLI invocations by the strategy; setup and judgement excluded",
    "agent_output_bytes": "bytes of stdout the agent had to read - a context-cost proxy, NOT tokens",
    "elapsed_s": "wall clock of the strategy only",
    "verdict": "PASS/FAIL/SKIP judged from the page state by an independent call",
}

#: Set by the shared interrupt handler; an interrupted run writes no receipt.
_INTERRUPT_STATE: dict = {}


class BenchConfigError(RuntimeError):
    """The bench cannot run at all: a missing suite, a missing fixture dir."""


class BenchRunError(RuntimeError):
    """A run failed in a way the bench did not plan for."""


# ===== the CLI, which is what an agent would actually type =====


@dataclass
class CliCall:
    """One CLI invocation and everything the bench keeps about it."""

    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    elapsed_s: float
    error: str | None = None

    @property
    def stdout_bytes(self) -> int:
        return len(self.stdout.encode("utf-8"))

    @property
    def ok(self) -> bool:
        return self.error is None and self.exit_code == 0


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _cli(*args: str, timeout: float = CLI_TIMEOUT_S) -> CliCall:
    """Run one ``eyes`` CLI invocation.

    The bench drives the CLI rather than the Python API because the metric
    being measured is what an agent would type and read.
    """
    argv = [sys.executable, "-m", "openeyes.cli.main", *args]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(REPO_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return CliCall(
            argv, None, _as_text(exc.stdout), _as_text(exc.stderr),
            time.perf_counter() - started,
            error=f"timeout after {timeout:.0f}s",
        )
    except OSError as exc:
        return CliCall(argv, None, "", "", time.perf_counter() - started, error=str(exc))
    return CliCall(
        argv, completed.returncode, completed.stdout or "", completed.stderr or "",
        time.perf_counter() - started,
    )


def _json_payload(text: str) -> dict | None:
    """Parse a JSON object out of CLI stdout; ``None`` when there is none."""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _json_list(text: str) -> list:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def _tail(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


# ===== the browser this bench started, and only that one =====


def _pid_alive(pid: int) -> bool:
    """Whether the pid is still running (Windows, without psutil)."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000 | 0x00001000, False, int(pid))
    if not handle:
        return False
    try:
        status = kernel32.WaitForSingleObject(handle, 0)
    finally:
        kernel32.CloseHandle(handle)
    return status == 0x00000102  # WAIT_TIMEOUT -> still running


def _taskkill(pid: int, *, force: bool) -> None:
    """Stop one process tree - the Edge this bench started, and nothing else."""
    argv = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        argv.append("/F")
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(argv, capture_output=True, text=True, timeout=30)


class _EdgeProcess:
    """A Popen-shaped adapter so the shared ``_stop_process`` can stop Edge.

    ``launch_edge`` hands back a pid, not a ``Popen``; the shared stop helper
    wants ``poll`` / ``terminate`` / ``wait`` / ``stderr``.  Only the process
    this bench started is ever handed over, so only that tree is ever killed.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.stderr = io.StringIO("")

    def poll(self) -> int | None:
        return None if _pid_alive(self.pid) else 0

    def terminate(self) -> None:
        _taskkill(self.pid, force=False)

    def kill(self) -> None:
        _taskkill(self.pid, force=True)

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.time() + (5.0 if timeout is None else timeout)
        while time.time() < deadline:
            if not _pid_alive(self.pid):
                return 0
            time.sleep(0.1)
        return 0


def _launch_edge(port: int):
    """Start a visible Edge with remote debugging on ``port``."""
    from openeyes.backends import cdp

    profile_dir = Path(tempfile.gettempdir()) / "openeyes-bench" / f"edge-profile-{port}"
    info = cdp.launch_edge(
        port=port, seed=False, headless=False, profile_dir=profile_dir,
    )
    return info, _EdgeProcess(int(info["pid"]))


def _wait_for_page(port: int, token: str, timeout: float = 25.0) -> str:
    """Wait until the fixture tab is on its URL and the document is parsed.

    This is CDP plumbing, not a CLI call: it belongs to the "open the tab"
    setup step and is not agent-facing, so it is not a call in any bucket.
    """
    from openeyes.backends import cdp

    deadline = time.time() + timeout
    state = ""
    while time.time() < deadline:
        conn = None
        try:
            conn = cdp.connect(port=port, url_contains=token)
            state = "the tab is not open yet"
        except Exception as exc:
            state = f"the tab is not open yet: {exc}"
        if conn is not None:
            try:
                ready = conn.evaluate("document.readyState")
                url = conn.current_url()
                if ready in ("interactive", "complete") and token in (url or ""):
                    return ready
                state = f"readyState={ready!r} url={url!r}"
            except Exception as exc:
                state = f"not ready yet: {exc}"
            finally:
                with contextlib.suppress(Exception):
                    conn.close()
        time.sleep(0.2)
    raise BenchRunError(f"the fixture page did not become ready ({state or 'no answer'})")


# ===== the task set (written by the lead, loaded here) =====


def _load_suite(name: str) -> list:
    """Load one suite's tasks.  A missing suite is an error, never a fallback."""
    module_name = SUITE_MODULES.get(name)
    if module_name is None:
        known = ", ".join(SUITE_MODULES)
        raise BenchConfigError(f"unknown suite {name!r}; known suites: {known}")
    path = BENCH_DIR / f"{module_name}.py"
    if not path.is_file():
        raise BenchConfigError(
            f"the {name!r} suite is missing: {path} is not there. "
            f"The suite is written by the lead; the bench loads it and never invents one."
        )
    spec = importlib.util.spec_from_file_location(f"_browser_task_suite_{name}", path)
    if spec is None or spec.loader is None:
        raise BenchConfigError(f"the {name!r} suite at {path} cannot be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    loader = getattr(module, "load_tasks", None)
    if loader is None:
        raise BenchConfigError(f"{path} does not expose load_tasks()")
    return list(loader())


def _task_id(task) -> str:
    return str(getattr(task, "id", "") or "")


def _task_targets(task) -> list[str]:
    return [str(item) for item in (getattr(task, "targets", ()) or ())]


def _task_input_text(task) -> str:
    return str(getattr(task, "input_text", "") or "")


def _task_requires(task) -> list[str]:
    return [str(item) for item in (getattr(task, "requires", ()) or ())]


def _task_expect_js(task) -> str:
    return str(getattr(task, "expect_js", "") or "")


# ===== the routes =====


@dataclass
class _Ctx:
    """What a route is allowed to use: the port, the token, the page."""

    port: int
    token: str
    page_url: str
    calls: list[CliCall] = field(default_factory=list)

    def call(self, *args: str) -> CliCall:
        call = _cli(*args)
        self.calls.append(call)
        return call


@dataclass
class _Outcome:
    """What a route did.  Deliberately carries no verdict: it cannot report one."""

    notes: list[str] = field(default_factory=list)
    skip_reason: str | None = None
    error: str | None = None


def _first_matching(elements: list[dict], target: str) -> dict | None:
    """The element ``--name-contains <target>`` would pick.

    The CLI matches case-insensitively on a substring and takes the first
    element in DOM order; the bench mirrors that so both routes address the
    same element.
    """
    needle = target.casefold()
    for element in elements:
        name = str(element.get("name") or "")
        if needle in name.casefold():
            return element
    return None


def _is_editable(element: dict) -> bool:
    """Whether ``browser type`` is the right verb for this element.

    The CDP probe reports ``control_type`` as ``tag.title()`` (``Input``,
    ``Textarea``), not as the UI-automation labels (``Edit``), so the tag and
    the input ``type`` are the authoritative signals here.  Judging editability
    by control type alone made every form field look click-only.
    """
    tag = str(element.get("tag") or "").casefold()
    if tag == "textarea":
        return True
    if tag == "input":
        return str(element.get("type") or "text").casefold() not in NON_TEXT_INPUT_TYPES
    if str(element.get("control_type") or "") in EDITABLE_CONTROL_TYPES:
        return True
    class_name = str(element.get("class_name") or "").casefold()
    return "contenteditable" in class_name


def _scan_elements(call: CliCall) -> list[dict]:
    """The scan's elements.  A scan that failed yields no elements."""
    if not call.ok:
        return []
    return [item for item in _json_list(call.stdout) if isinstance(item, dict)]


def _gate_verbs(task) -> str | None:
    """Why the verbs route cannot run this task - decided before any call."""
    unsupported = [c for c in _task_requires(task) if c not in VERBS_CAPABILITIES]
    if unsupported:
        return (
            "the verbs route has no verb for "
            + ", ".join(repr(c) for c in unsupported)
            + " - the fixed menu is scan / click / type"
        )
    if not _task_targets(task) and not _task_input_text(task):
        return (
            "the verbs route cannot express this task: there is no target to "
            "click and no text to type"
        )
    return None


def _gate_code(task) -> str | None:
    """Why the code route cannot run this task - decided before any call."""
    unsupported = [c for c in _task_requires(task) if c not in CODE_CAPABILITIES]
    if unsupported:
        return (
            "the code route has no plan for "
            + ", ".join(repr(c) for c in unsupported)
            + " - it can click, type and read text"
        )
    return None


def _gate_control(task) -> str | None:
    """The control route can always run: it does nothing."""
    return None


def _run_verbs(task, ctx: _Ctx) -> _Outcome:
    """The fixed menu: one scan, then one call per action."""
    outcome = _Outcome()
    targets = _task_targets(task)
    text = _task_input_text(task)
    scan = ctx.call(
        "browser", "scan", "--port", str(ctx.port), "--url-contains", ctx.token,
    )
    elements = _scan_elements(scan)
    outcome.notes.append(
        f"scan: exit {scan.exit_code}, {len(elements)} interactive elements, "
        f"{scan.stdout_bytes} stdout bytes"
    )
    for target in targets:
        element = _first_matching(elements, target)
        if element is None:
            # The fixed menu addresses controls by name through the scan.
            # When the scan cannot see one, the route cannot express the task:
            # that is SKIP with a reason, never FAIL (spec 4.5), and a blind
            # ``--name-contains`` click would be a guess, not a route.
            outcome.skip_reason = (
                f"scan reports {len(elements)} interactive element(s) and none "
                f"matches {target!r} - the fixed menu cannot address it"
            )
            outcome.notes.append(
                f"scan: no element matches {target!r} - stopped before clicking"
            )
            break
        if text and _is_editable(element):
            call = ctx.call(
                "browser", "type", "--text", text, "--name-contains", target,
                "--port", str(ctx.port), "--url-contains", ctx.token,
            )
            outcome.notes.append(f"type {target!r}: exit {call.exit_code}")
        else:
            call = ctx.call(
                "browser", "click", "--name-contains", target, "--go", "--json",
                "--port", str(ctx.port), "--url-contains", ctx.token,
            )
            outcome.notes.append(f"click {target!r}: exit {call.exit_code}")
    return outcome


#: The code route's page JavaScript.  It walks the task's targets in one go:
#: an editable target is typed into, every other target is clicked on the
#: element itself (not at a coordinate), which is why it can act through an
#: overlay that swallows the click the fixed menu sends.
_CODE_ROUTE_JS = r"""
(function () {
  var TARGETS = __TARGETS__;
  var TEXT = __TEXT__;
  var NEEDS_READ = __NEEDS_READ__;
  var INTERACTIVE = "a[href],button,input,textarea,select,[contenteditable]," +
    "[tabindex],[role=button],[role=link],[role=checkbox],[role=radio]," +
    "[role=tab],[role=menuitem],summary,label";

  function nameOf(el) {
    var al = el.getAttribute("aria-label");
    if (al && al.trim()) return al.trim();
    var ph = el.getAttribute("placeholder");
    if (ph && ph.trim()) return ph.trim();
    var tt = el.getAttribute("title");
    if (tt && tt.trim()) return tt.trim();
    var alt = el.getAttribute("alt");
    if (alt && alt.trim()) return alt.trim();
    var txt = (el.innerText || el.textContent || "").trim();
    if (txt) return txt.replace(/\s+/g, " ").slice(0, 200);
    var nm = el.getAttribute("name");
    if (nm) return nm;
    return el.id || "";
  }

  function visible(el) {
    if (!el.isConnected) return false;
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    var style = el.ownerDocument.defaultView.getComputedStyle(el);
    return !(style.visibility === "hidden" || style.display === "none");
  }

  function editable(el) {
    var tag = el.tagName.toLowerCase();
    if (tag === "textarea") return true;
    if (tag === "input") {
      var type = (el.getAttribute("type") || "text").toLowerCase();
      return ["checkbox", "radio", "button", "submit", "reset", "file",
              "image", "range", "color"].indexOf(type) < 0;
    }
    return el.isContentEditable === true;
  }

  function documents() {
    var docs = [document];
    var frames = document.querySelectorAll("iframe");
    for (var i = 0; i < frames.length; i++) {
      try {
        var doc = frames[i].contentDocument;
        if (doc) docs.push(doc);
      } catch (err) { /* a cross-origin frame is out of reach, and out of scope */ }
    }
    return docs;
  }

  function elements() {
    var out = [];
    var docs = documents();
    for (var d = 0; d < docs.length; d++) {
      var doc = docs[d];
      var nodes = doc.querySelectorAll(INTERACTIVE);
      for (var i = 0; i < nodes.length; i++) {
        if (visible(nodes[i])) out.push(nodes[i]);
      }
      var hosts = doc.querySelectorAll("*");
      for (var h = 0; h < hosts.length; h++) {
        var root = hosts[h].shadowRoot;
        if (!root) continue;
        var inner = root.querySelectorAll(INTERACTIVE);
        for (var j = 0; j < inner.length; j++) {
          if (visible(inner[j])) out.push(inner[j]);
        }
      }
    }
    return out;
  }

  function findByName(target) {
    var needle = String(target).toLowerCase();
    var all = elements();
    for (var i = 0; i < all.length; i++) {
      if (nameOf(all[i]).toLowerCase().indexOf(needle) >= 0) return all[i];
    }
    return null;
  }

  function setValue(el, value) {
    try { el.focus(); } catch (err) { }
    try { el.value = value; } catch (err) { }
    var view = el.ownerDocument.defaultView;
    el.dispatchEvent(new view.Event("input", { bubbles: true }));
    el.dispatchEvent(new view.Event("change", { bubbles: true }));
  }

  function readText() {
    var selectors = ["tbody tr", "tr", "[role=row]", "li", "[class*=row]",
                     "[class*=item]", "p"];
    for (var s = 0; s < selectors.length; s++) {
      var nodes = document.querySelectorAll(selectors[s]);
      var texts = [];
      for (var i = 0; i < nodes.length; i++) {
        var text = (nodes[i].innerText || nodes[i].textContent || "").trim();
        if (text) texts.push(text.replace(/\s+/g, " "));
      }
      if (texts.length) return texts.join("\n");
    }
    return (document.body && document.body.innerText || "").trim();
  }

  var report = { clicked: [], typed: [], missing: [], read: null };
  if (NEEDS_READ) report.read = readText();
  for (var t = 0; t < TARGETS.length; t++) {
    var target = TARGETS[t];
    var el = findByName(target);
    if (!el) { report.missing.push(target); continue; }
    if (editable(el)) {
      var value = TEXT;
      if (NEEDS_READ && !value && report.read) value = report.read;
      setValue(el, value);
      report.typed.push(target);
    } else {
      el.click();
      report.clicked.push(target);
    }
  }
  return report;
})()
""".strip()


def _code_route_js(task) -> str:
    """The code route's script, precomputed from the task's own fields."""
    script = _CODE_ROUTE_JS
    script = script.replace("__TARGETS__", json.dumps(_task_targets(task)))
    script = script.replace("__TEXT__", json.dumps(_task_input_text(task)))
    script = script.replace(
        "__NEEDS_READ__",
        "true" if "read_text" in _task_requires(task) else "false",
    )
    return script


def _run_code(task, ctx: _Ctx) -> _Outcome:
    """The code-first route: one call whose JavaScript loops."""
    outcome = _Outcome()
    script = _code_route_js(task)
    call = ctx.call(
        "browser", "exec", "--code", script, "--go", "--json",
        "--port", str(ctx.port), "--url-contains", ctx.token,
    )
    outcome.notes.append(
        f"exec: exit {call.exit_code}, {len(script)} chars of page JavaScript, "
        f"{call.stdout_bytes} stdout bytes"
    )
    return outcome


def _run_control(task, ctx: _Ctx) -> _Outcome:
    """The do-nothing route.  It must fail every task."""
    outcome = _Outcome()
    call = ctx.call(
        "browser", "exec", "--code", "void 0", "--go", "--json",
        "--port", str(ctx.port), "--url-contains", ctx.token,
    )
    outcome.notes.append(f"control: exit {call.exit_code}, the page was not touched")
    return outcome


_GATES = {
    "verbs": _gate_verbs,
    "code": _gate_code,
    "control": _gate_control,
}

_RUNNERS = {
    "verbs": _run_verbs,
    "code": _run_code,
    "control": _run_control,
}


# ===== the judgement: the only source of a verdict =====


def _normalize(value):
    """A JSON-comparable form of a value (tuples become lists)."""
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    return value


def _matches(result, expected) -> bool:
    return _normalize(result) == _normalize(expected)


def _read_expectation(task, ctx: _Ctx) -> tuple[object, str | None, CliCall]:
    """Read the task's expectation out of the page.

    The value comes from the page, never from the route.  This call is the
    bench's, not the route's: it is counted as a setup call (before the route
    runs) or a judgement call (after it), never as a route call.
    """
    call = _cli(
        "browser", "exec", "--code", _task_expect_js(task), "--go", "--json",
        "--port", str(ctx.port), "--url-contains", ctx.token,
    )
    if call.error:
        return None, f"the expectation could not be read: {call.error}", call
    if call.exit_code != 0:
        return None, (
            f"the expectation could not be read: exit {call.exit_code}: "
            f"{_tail(call.stderr)}"
        ), call
    payload = _json_payload(call.stdout)
    if payload is None:
        return None, "the expectation read returned no JSON", call
    if "result" not in payload:
        return None, "the expectation read returned no result", call
    return payload["result"], None, call


# ===== one measurement =====


def _call_summary(call: CliCall) -> dict:
    """A compact record of one CLI call (the script itself is summarised)."""
    argv = list(call.argv[3:])
    if "--code" in argv:
        index = argv.index("--code")
        code = argv[index + 1] if index + 1 < len(argv) else ""
        digest = hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]
        argv = argv[:index] + [f"<{len(code)} chars of page JavaScript, sha1 {digest}>"] \
            + argv[index + 2:]
    return {
        "argv": argv,
        "exit_code": call.exit_code,
        "stdout_bytes": call.stdout_bytes,
        "error": call.error,
    }


def _measure_once(
    task, strategy: str, *, port: int, fixture_port: int,
) -> tuple[dict, list[str]]:
    """Measure one (task, strategy) pair: setup, route, judgement, teardown."""
    errors: list[str] = []
    task_id = _task_id(task)
    row: dict = {
        "task": task_id,
        "strategy": strategy,
        "calls": 0,
        "agent_output_bytes": 0,
        "elapsed_s": 0.0,
        "setup_calls": 0,
        "judgement_calls": 0,
        "verdict": FAIL,
        "reason": "",
        "detail": {},
    }

    skip_reason = _GATES[strategy](task)
    if skip_reason:
        row["verdict"] = SKIP
        row["reason"] = skip_reason
        row["detail"]["setup_calls_detail"] = ["none - the route was skipped before it touched the browser"]
        return row, errors

    page_file = PAGES_DIR / str(getattr(task, "page", "") or "")
    if not page_file.is_file():
        reason = f"the fixture page is missing: {page_file}"
        row["verdict"] = FAIL
        row["reason"] = reason
        errors.append(f"{task_id}/{strategy}: {reason}")
        return row, errors

    token = "bench-" + uuid.uuid4().hex[:12]
    page_url = f"http://127.0.0.1:{fixture_port}/{page_file.name}?bench={token}"
    ctx = _Ctx(port=port, token=token, page_url=page_url)
    row["detail"]["page"] = page_file.name
    row["detail"]["page_url"] = page_url
    tab_id = ""
    try:
        tab_id = _open_tab(port, page_url)
        row["setup_calls"] = 1
        _wait_for_page(port, token)

        initial, error, _ = _read_expectation(task, ctx)
        row["setup_calls"] = 2
        if error:
            row["verdict"] = FAIL
            row["reason"] = error
            errors.append(f"{task_id}/{strategy}: {error}")
            return row, errors
        if _matches(initial, getattr(task, "expected", None)):
            reason = "the page already satisfied the expectation before the route ran"
            row["verdict"] = FAIL
            row["reason"] = reason
            row["detail"]["pre_state"] = "already-satisfied"
            row["detail"]["observed"] = initial
            errors.append(f"{task_id}: {reason}")
            return row, errors

        started = time.perf_counter()
        outcome = _RUNNERS[strategy](task, ctx)
        row["elapsed_s"] = round(time.perf_counter() - started, 3)
        row["calls"] = len(ctx.calls)
        row["agent_output_bytes"] = sum(call.stdout_bytes for call in ctx.calls)
        row["detail"]["route_notes"] = outcome.notes
        row["detail"]["calls_detail"] = [_call_summary(call) for call in ctx.calls]
        row["detail"]["failed_calls"] = sum(1 for call in ctx.calls if not call.ok)
        row["detail"]["setup_calls_detail"] = ["open-tab", "initial-state-read"]
        if outcome.error:
            errors.append(f"{task_id}/{strategy}: {outcome.error}")
        if outcome.skip_reason:
            # The route ran and learned it cannot address the task.  The cost of
            # learning that is real, so it stays in the row; the verdict is SKIP
            # and no judgement call is spent (spec 4.5).
            row["verdict"] = SKIP
            row["reason"] = outcome.skip_reason
            row["detail"]["skip_after_calls"] = len(ctx.calls)
            return row, errors

        time.sleep(SETTLE_S)
        result, error, _ = _read_expectation(task, ctx)
        row["judgement_calls"] = 1
        if error:
            row["verdict"] = FAIL
            row["reason"] = error
            errors.append(f"{task_id}/{strategy}: {error}")
            return row, errors
        if _matches(result, getattr(task, "expected", None)):
            row["verdict"] = PASS
        else:
            row["verdict"] = FAIL
            row["reason"] = "the page state after the route did not match the expectation"
            row["detail"]["observed"] = result
        return row, errors
    except BenchRunError as exc:
        row["verdict"] = FAIL
        row["reason"] = str(exc)
        errors.append(f"{task_id}/{strategy}: {exc}")
        return row, errors
    finally:
        if tab_id:
            _close_tab(port, tab_id)


# ===== the receipt =====


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile: no interpolation, no invented smoothness."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(pct / 100.0 * len(ordered)) - 1)
    return round(ordered[rank], 3)


def _combine_runs(runs: list[dict]) -> dict:
    """Collapse the repetitions of one (task, strategy) into a single row."""
    if len(runs) == 1:
        return runs[0]
    verdicts = [run["verdict"] for run in runs]
    if all(verdict == SKIP for verdict in verdicts):
        verdict = SKIP
    elif any(verdict == FAIL for verdict in verdicts):
        verdict = FAIL
    elif any(verdict == SKIP for verdict in verdicts):
        # Repetitions that disagree are not a pass: a route that sometimes
        # cannot address the task is not reliably PASS, and it did not fail
        # either.  SKIP is the honest word for "not settled".
        verdict = SKIP
    else:
        verdict = PASS
    elapsed = [run["elapsed_s"] for run in runs]
    judged = [run for run in runs if run["verdict"] in (PASS, FAIL)]
    passed = [run for run in judged if run["verdict"] == PASS]
    row = {
        "task": runs[0]["task"],
        "strategy": runs[0]["strategy"],
        "calls": int(round(sum(run["calls"] for run in runs) / len(runs))),
        "agent_output_bytes": int(
            round(sum(run["agent_output_bytes"] for run in runs) / len(runs))
        ),
        "elapsed_s": round(sum(elapsed) / len(elapsed), 3),
        "setup_calls": runs[0]["setup_calls"],
        "judgement_calls": int(
            round(sum(run["judgement_calls"] for run in runs) / len(runs))
        ),
        "verdict": verdict,
        "reason": next((run["reason"] for run in runs if run["reason"]), ""),
        "detail": {},
    }
    detail = row["detail"]
    detail["runs"] = [
        {
            "verdict": run["verdict"],
            "calls": run["calls"],
            "agent_output_bytes": run["agent_output_bytes"],
            "elapsed_s": run["elapsed_s"],
            "failed_calls": run["detail"].get("failed_calls", 0),
        }
        for run in runs
    ]
    detail["calls_detail_note"] = (
        "omitted when --repeat > 1: every repetition issues the same argv apart "
        "from the per-run token; per-run cost is in detail.runs"
    )
    detail["repeat"] = len(runs)
    detail["elapsed_p50_s"] = _percentile(elapsed, 50)
    detail["elapsed_p90_s"] = _percentile(elapsed, 90)
    detail["success_rate"] = (
        round(len(passed) / len(judged), 3) if judged else None
    )
    if len(set(verdicts)) > 1:
        detail["disagreement"] = (
            f"{verdicts.count(PASS)} pass, {verdicts.count(FAIL)} fail, "
            f"{verdicts.count(SKIP)} skip across {len(verdicts)} repetitions"
        )
        row["reason"] = row["reason"] or detail["disagreement"]
    for key in ("page", "page_url", "route_notes", "observed", "pre_state"):
        if key in runs[0]["detail"]:
            detail[key] = runs[0]["detail"][key]
    return row


def _measure(
    task, strategy: str, *, port: int, fixture_port: int, repeat: int = 1,
) -> tuple[dict, list[str]]:
    """Measure one pair, ``repeat`` times, and report the aggregate."""
    runs: list[dict] = []
    errors: list[str] = []
    for _ in range(max(1, repeat)):
        row, row_errors = _measure_once(
            task, strategy, port=port, fixture_port=fixture_port
        )
        runs.append(row)
        errors.extend(row_errors)
    return _combine_runs(runs), errors


def _summary(rows: list[dict]) -> dict:
    """Per-strategy totals plus the two invariants the bench has to report."""
    by_strategy: dict = {}
    for row in rows:
        bucket = by_strategy.setdefault(row["strategy"], {
            "tasks": 0, "pass": 0, "fail": 0, "skip": 0,
            "calls": 0, "agent_output_bytes": 0, "elapsed_s": 0.0,
        })
        bucket["tasks"] += 1
        bucket[row["verdict"].lower()] += 1
        bucket["calls"] += row["calls"]
        bucket["agent_output_bytes"] += row["agent_output_bytes"]
        bucket["elapsed_s"] += row["elapsed_s"]
    samples = [
        (row["strategy"], run)
        for row in rows
        for run in row.get("detail", {}).get("runs", [])
    ]
    for strategy, bucket in by_strategy.items():
        mine = [run for name, run in samples if name == strategy]
        if not mine:
            continue
        elapsed = [run["elapsed_s"] for run in mine]
        judged = [run for run in mine if run["verdict"] in (PASS, FAIL)]
        passed = [run for run in judged if run["verdict"] == PASS]
        bucket["runs"] = len(mine)
        bucket["pass_runs"] = len(passed)
        bucket["fail_runs"] = len(judged) - len(passed)
        bucket["skip_runs"] = len(mine) - len(judged)
        bucket["elapsed_p50_s"] = _percentile(elapsed, 50)
        bucket["elapsed_p90_s"] = _percentile(elapsed, 90)
        bucket["success_rate"] = round(len(passed) / len(judged), 3) if judged else None
    for bucket in by_strategy.values():
        bucket["elapsed_s"] = round(bucket["elapsed_s"], 3)

    control_rows = [row for row in rows if row["strategy"] == "control"]
    passed_controls = [row["task"] for row in control_rows if row["verdict"] == PASS]
    pre_state_violations = sorted({
        row["task"] for row in rows if row["detail"].get("pre_state") == "already-satisfied"
    })
    return {
        "by_strategy": by_strategy,
        "control_all_failed": bool(control_rows) and not passed_controls,
        "control_runs": len(control_rows),
        "control_passed_tasks": passed_controls,
        "pre_state_ok": not pre_state_violations,
        "pre_state_violations": pre_state_violations,
    }


def _bench_verdict(rows: list[dict], summary: dict, errors: list[str]) -> tuple[str, str]:
    """The bench's own verdict: about the bench, not about the routes."""
    passed_controls = summary["control_passed_tasks"]
    if passed_controls:
        return FAIL, (
            "the control route passed "
            + ", ".join(passed_controls)
            + " - a control that passes means the bench cannot tell a real pass "
            "from a page that was already done"
        )
    violations = summary["pre_state_violations"]
    if violations:
        return FAIL, (
            "the page already satisfied the expectation before the route ran: "
            + ", ".join(violations)
        )
    if errors:
        shown = "; ".join(errors[:3])
        if len(errors) > 3:
            shown += f" (+{len(errors) - 3} more)"
        return FAIL, f"unexpected error(s): {shown}"
    if not [row for row in rows if row["verdict"] in (PASS, FAIL)]:
        return SKIP, "nothing could be judged"
    return PASS, ""


def _now() -> str:
    """A local timestamp with the offset, e.g. ``2026-11-24T15:04:05+08:00``."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _default_out_dir() -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return str(Path(tempfile.gettempdir()) / "openeyes-bench" / f"browser-task-bench-{stamp}")


# ===== the run =====


def _parse_list(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _collect_tasks(suites: list[str], wanted: list[str]) -> list:
    """Load the requested suites and apply the task filter."""
    tasks: list = []
    for suite in suites:
        tasks.extend(_load_suite(suite))
    if wanted:
        known = {_task_id(task) for task in tasks}
        missing = [task_id for task_id in wanted if task_id not in known]
        if missing:
            raise BenchConfigError(
                "unknown task id(s): " + ", ".join(missing)
                + "; the known ids are: " + (", ".join(sorted(known)) or "(none)")
            )
        tasks = [task for task in tasks if _task_id(task) in set(wanted)]
    if not tasks:
        raise BenchConfigError("no tasks to run")
    return tasks


def _print_human(receipt: dict) -> None:
    print(f"browser task bench - {receipt['bench_verdict']}")
    for strategy, bucket in receipt["summary"]["by_strategy"].items():
        print(
            f"  {strategy:<8} {bucket['tasks']} task(s): {bucket['pass']} pass, "
            f"{bucket['fail']} fail, {bucket['skip']} skip | "
            f"{bucket['calls']} call(s) | {bucket['agent_output_bytes']} stdout bytes | "
            f"{bucket['elapsed_s']}s"
            + (
                f" | p50 {bucket['elapsed_p50_s']}s p90 {bucket['elapsed_p90_s']}s"
                f" | success {bucket['success_rate']}"
                if "elapsed_p90_s" in bucket else ""
            )
        )
    if receipt.get("bench_reason"):
        print(f"  reason: {receipt['bench_reason']}")
    for row in receipt["tasks"]:
        if row["verdict"] != PASS:
            print(f"  {row['task']} / {row['strategy']}: {row['verdict']} - {row['reason']}")


def _run(args) -> int:
    """Run the bench and write the receipt.  Returns the process exit code."""
    out_dir = _resolve_out_dir(args.out or _default_out_dir())
    _require_free_evidence(out_dir, [RECEIPT_NAME])

    suites = _parse_list(args.suite) or ["core"]
    strategies = _parse_list(args.strategies) or list(STRATEGIES)
    unknown = [s for s in strategies if s not in STRATEGIES]
    if unknown:
        raise BenchConfigError(
            f"unknown strateg(y|ies): {', '.join(unknown)}; known: {', '.join(STRATEGIES)}"
        )
    if not PAGES_DIR.is_dir():
        raise BenchConfigError(
            f"the fixture pages are missing: {PAGES_DIR} is not there. "
            f"The pages are written by the lead; the bench serves them and never invents one."
        )
    tasks = _collect_tasks(suites, _parse_list(args.tasks))
    repeat = max(1, int(getattr(args, "repeat", 1) or 1))

    server, fixture_port = _start_fixture_server(PAGES_DIR)
    edge = None
    rows: list[dict] = []
    errors: list[str] = []
    try:
        if args.no_launch:
            if not _port_listening(args.port):
                raise BenchConfigError(
                    f"--no-launch was given but nothing is listening on port {args.port}"
                )
        else:
            _, edge = _launch_edge(args.port)
        for task in tasks:
            for strategy in strategies:
                row, row_errors = _measure(
                    task, strategy, port=args.port, fixture_port=fixture_port,
                    repeat=repeat,
                )
                rows.append(row)
                errors.extend(row_errors)
    finally:
        if edge is not None:
            _stop_process(edge)
        with contextlib.suppress(Exception):
            server.shutdown()

    summary = _summary(rows)
    verdict, reason = _bench_verdict(rows, summary, errors)
    metric_notes = dict(METRIC_NOTES)
    if repeat > 1:
        metric_notes["elapsed_p50_s"] = (
            f"nearest-rank median of {repeat} repetitions of the same (task, strategy)"
        )
        metric_notes["elapsed_p90_s"] = (
            f"nearest-rank p90 of {repeat} repetitions of the same (task, strategy)"
        )
        metric_notes["success_rate"] = (
            "PASS runs / judged runs across the repetitions; SKIP runs are not judged"
        )
    receipt = {
        "bench": "browser_task",
        "generated_at": _now(),
        "port": args.port,
        "repeat": repeat,
        "suites": suites,
        "metric_notes": metric_notes,
        "tasks": rows,
        "summary": summary,
        "bench_verdict": verdict,
        "bench_reason": reason,
        "errors": errors,
    }
    _require_not_interrupted(_INTERRUPT_STATE)
    receipt_path = _write_receipt_atomic(out_dir / RECEIPT_NAME, receipt)
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    else:
        _print_human(receipt)
        print(f"  receipt: {receipt_path}")
    return EXIT_CODES[verdict]


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bench_browser_task.py",
        description=(
            "Measure what each browser route costs on the same task set: "
            "the fixed menu (verbs), the code-first escape hatch (code) and a "
            "do-nothing control that must fail every task."
        ),
    )
    parser.add_argument("--port", type=int, default=9333,
                        help="CDP port (default: 9333)")
    parser.add_argument("--suite", default="core",
                        help="comma list of suites to run: core, extra (default: core)")
    parser.add_argument("--tasks", default="",
                        help="comma list of task ids to run (default: all)")
    parser.add_argument("--strategies", default=",".join(STRATEGIES),
                        help="comma list of strategies: verbs, code, control")
    parser.add_argument("--out", default=None,
                        help="directory for the JSON receipt (default: a fresh temp directory)")
    parser.add_argument("--json", action="store_true",
                        help="print the receipt object on stdout")
    parser.add_argument("--no-launch", action="store_true",
                        help="use a browser that is already listening; do not kill it")
    parser.add_argument("--repeat", type=int, default=1,
                        help="repetitions per (task, strategy); >1 adds p50/p90 and "
                             "a success rate (default: 1)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    _configure_stdout()
    _install_interrupt_handler(_INTERRUPT_STATE)
    try:
        return _run(args)
    except KeyboardInterrupt:
        print("interrupted: no receipt was written", file=sys.stderr)
        return EXIT_INTERRUPTED
    except OutDirError as exc:
        return _out_dir_failure("browser-task-bench", exc)
    except BenchConfigError as exc:
        print(json.dumps({
            "bench": "browser_task",
            "verdict": FAIL,
            "reason": "configuration",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CODES[FAIL]


if __name__ == "__main__":
    raise SystemExit(main())
