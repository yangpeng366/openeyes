"""Replayer: turn a Trajectory back into OpenEyes actuate() calls - and prove
that each call took effect.

Strategy:
- For each step, derive a selector (built at capture time) and feed it to
  OpenEyes's click_by_selector / send_hotkey / type_text.
- For selectors without a structured key, fall back to bbox center click.
- Real replay (``dry_run=False``) reports one of three states per step:

  ``verified``    an expectation recorded in the trajectory was *observed*
                  to change from false to true across the action
                  (``act_until`` polling);
  ``unverified``  the action was delivered, but nothing judgeable was
                  recorded - or the expectation was already true before
                  the action - so there is no proof that the state changed;
  ``failed``      the expectation never held within the timeout, or the
                  action could not be delivered at all.

  ``sent=True`` only ever means "delivered". Delivery is not effect.

Where an expectation comes from
-------------------------------
Only fields that a machine can re-check at replay time are used:

- ``meta.expect`` / ``meta.verify`` - an explicit spec, either a string
  (substring of some element name) or a dict::

      {"automation_id": "btn_submit"}                 # element now exists
      {"name_contains": "Saved", "control_type": "Text"}
      {"present": false, "automation_id": "spinner"}  # element went away
      {"window_title_contains": "Settings"}

  ``timeout`` / ``interval`` / ``retries`` keys override the replayer
  defaults for that step.
- ``meta.expect_window_title`` - shorthand for a window-title expectation.

These fields are *not* treated as expectations, because observing them after
the action proves nothing:

- ``window_title`` / ``app_name`` / ``pid`` / ``hwnd``: capture-time context.
  The window is normally already up when replay starts, so "the title is
  still there" is true before and after the click.
- ``intent``: natural language, not machine-judgeable.
- ``input_text``: a keystroke is not proof the value landed in the right
  control (masked fields, focus theft, IME), so typing is not verified
  against its own text.
- ``element.state`` / ``bbox`` / ``selector``: capture-time snapshots of the
  thing we are about to click, not a post-condition.

A trajectory captured by :class:`~openeyes.record.recorder.Recorder` today
carries no ``meta.expect``, so its steps can only come back ``unverified``.
Pass ``meta={"expect": {...}}`` when recording to make a step provable.

Retry policy (never silent)
---------------------------
A pointer action retries through the *real* strategy ladder: UIA selector
first, bbox center second. Every attempt is recorded in ``paths`` and the
winning rung in ``path``; there is no hidden retry loop. A ``type`` step is
re-focused but never re-typed on a later attempt (typing twice appends the
text twice), which is reported with ``text_typed``.

`dry_run=True` prints every planned actuate() call without sending real
input - safe to run on any machine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from openeyes.core.verify import act_until

from .event import InputEvent, Trajectory, UIAEvent

STATUS_VERIFIED = "verified"
STATUS_UNVERIFIED = "unverified"
STATUS_FAILED = "failed"
STATUS_PLANNED = "planned"
STATUS_SKIPPED = "skipped"

_ELEMENT_KEYS = ("automation_id", "name_contains", "control_type", "regex")
_TEXT_ALIASES = ("text", "text_contains", "name")
_WINDOW_KEYS = ("window_title_contains", "window_title", "title")


def _import_openeyes():
    """Import the OpenEyes primitive surface.

    Kept behind a function so tests can prove what happens when the package
    is not importable without touching a real desktop.
    """
    import openeyes

    return openeyes


# ---------------------------------------------------------------------------
# Selector / plan helpers
# ---------------------------------------------------------------------------

def _selector_filters(selector: str) -> dict[str, str] | None:
    """Turn a capture-time selector string into find_elements() kwargs.

    ``None`` means "this selector is not addressable over UIA" - the caller
    must fall back to the bbox center.
    """
    if not selector:
        return None
    if selector.startswith("automation_id="):
        value = selector.split("=", 1)[1].strip()
        return {"automation_id": value} if value else None
    if selector.startswith("name="):
        parts = selector[len("name="):].split("|")
        name = parts[0].strip()
        filters: dict[str, str] = {"name_contains": name} if name else {}
        for part in parts[1:]:
            if part.startswith("type="):
                ctype = part[len("type="):].strip()
                if ctype:
                    filters["control_type"] = ctype
        return filters or None
    # hint=..., center=(x,y), pointer=(x,y), combo=unknown: no UIA key.
    return None


def _bbox_xy(element: dict[str, Any] | None) -> tuple[int, int] | None:
    center = (element or {}).get("center") or {}
    x, y = center.get("x"), center.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return int(x), int(y)
    return None


def _combo_keys(combo: str) -> list[str]:
    return [part.strip() for part in (combo or "").split("+") if part.strip()]


def _click_flags(step: UIAEvent) -> tuple[str, bool]:
    meta = step.meta or {}
    button = str(meta.get("button") or "left")
    if step.action.startswith("right"):
        button = "right"
    double = bool(meta.get("double")) or step.action.startswith("double")
    return button, double


def _describe_actuate(step) -> dict[str, Any] | None:
    """Translate one step into an actuate() plan dict."""
    if isinstance(step, InputEvent):
        if step.action == "hotkey":
            return {"kind": "hotkey", "combo": step.combo, "intent": step.intent}
        if step.action == "wait":
            return {"kind": "wait", "ms": step.duration_ms or 500, "intent": step.intent}
        return None

    if isinstance(step, UIAEvent):
        ed = step.element
        if step.action == "click" or step.action.endswith("_click"):
            button, double = _click_flags(step)
            return {
                "kind": "click",
                "selector": step.selector,
                "element": ed,
                "hwnd": int(step.hwnd or 0),
                "button": button,
                "double": double,
                "intent": step.intent,
                "meta": step.meta,
            }
        if step.action == "type":
            return {
                "kind": "type",
                "selector": step.selector,
                "element": ed,
                "hwnd": int(step.hwnd or 0),
                "text": step.input_text,
                "intent": step.intent,
            }
        if step.action == "scroll":
            meta = step.meta or {}
            return {"kind": "scroll", "dx": meta.get("dx", 0), "dy": meta.get("dy", 0)}
        if step.action == "drag":
            meta = step.meta or {}
            return {
                "kind": "drag",
                "from_selector": step.selector,
                "element": ed,
                "hwnd": int(step.hwnd or 0),
                "to": meta.get("to"),
                "intent": step.intent,
            }
    return None


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------

def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_expectation(raw: Any) -> dict[str, Any] | None:
    """Normalize an ``expect`` payload into a judgeable spec, or ``None``.

    A spec that no probe can falsify (empty dict, unknown keys) returns
    ``None`` so the step is honestly reported as unverified instead of
    quietly counting as a success.
    """
    if isinstance(raw, str):
        text = _clean_str(raw)
        if not text:
            return None
        return {"element": {"name_contains": text}, "present": True}
    if not isinstance(raw, dict):
        return None

    filters: dict[str, str] = {}
    nested = raw.get("element")
    if isinstance(nested, dict):
        for key in _ELEMENT_KEYS:
            value = _clean_str(nested.get(key))
            if value:
                filters[key] = value
    for key in _ELEMENT_KEYS:
        value = _clean_str(raw.get(key))
        if value:
            filters[key] = value
    if "name_contains" not in filters:
        for alias in _TEXT_ALIASES:
            value = _clean_str(raw.get(alias))
            if value:
                filters["name_contains"] = value
                break

    title = None
    for key in _WINDOW_KEYS:
        title = _clean_str(raw.get(key))
        if title:
            break

    if not filters and not title:
        return None

    present = raw.get("present", True)
    if raw.get("absent") is True:
        present = False
    spec: dict[str, Any] = {
        "element": filters,
        "present": bool(present),
    }
    if title:
        spec["window_title_contains"] = title
    for key in ("timeout", "interval"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and value > 0:
            spec[key] = float(value)
    retries = raw.get("retries")
    if isinstance(retries, int) and retries > 0:
        spec["retries"] = retries
    return spec


def expectation_from_step(step) -> tuple[dict[str, Any] | None, str]:
    """Return ``(spec, source)`` for one step.

    ``source`` names where the expectation came from, or why there is none -
    it is surfaced verbatim in the report so a missing proof is traceable.
    """
    meta = getattr(step, "meta", None) or {}
    if not isinstance(meta, dict):
        return None, "meta is not a mapping"
    for key in ("expect", "verify"):
        if key in meta:
            spec = normalize_expectation(meta[key])
            if spec is None:
                return None, f"meta.{key} is not judgeable: {meta[key]!r}"
            return spec, f"meta.{key}"
    title = _clean_str(meta.get("expect_window_title"))
    if title:
        return {
            "element": {},
            "present": True,
            "window_title_contains": title,
        }, "meta.expect_window_title"
    return None, "no expectation recorded"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class ReplayReport:
    trajectory_id: str
    trajectory_name: str
    dry_run: bool
    actions: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counts = {
            STATUS_VERIFIED: 0,
            STATUS_UNVERIFIED: 0,
            STATUS_FAILED: 0,
            STATUS_SKIPPED: 0,
            STATUS_PLANNED: 0,
        }
        for action in self.actions:
            status = action.get("status")
            if status in counts:
                counts[status] += 1
        counts[STATUS_SKIPPED] = len(self.skipped)
        return counts

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts()
        return {
            "trajectory_id": self.trajectory_id,
            "trajectory_name": self.trajectory_name,
            "dry_run": self.dry_run,
            "actions": self.actions,
            "skipped_count": len(self.skipped),
            "summary": {
                "total": len(self.actions) + len(self.skipped),
                "verified": counts[STATUS_VERIFIED],
                "unverified": counts[STATUS_UNVERIFIED],
                "failed": counts[STATUS_FAILED],
                "skipped": counts[STATUS_SKIPPED],
                "planned": counts[STATUS_PLANNED],
                # True only when every executed step was proven effective.
                "all_effect_verified": None if self.dry_run else (
                    counts[STATUS_VERIFIED] > 0
                    and counts[STATUS_FAILED] == 0
                    and counts[STATUS_UNVERIFIED] == 0
                ),
            },
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class OpenEyesBackend:
    """Thin adapter over the OpenEyes primitives (replaceable in tests)."""

    def __init__(self, module):
        self._oe = module

    # -- actuation --

    def click_selector(self, hwnd: int, selector: str, *, button: str = "left",
                       double: bool = False):
        filters = _selector_filters(selector)
        if filters is None:
            raise ValueError(f"selector is not addressable over UIA: {selector!r}")
        return self._oe.click_by_selector(
            int(hwnd or 0), button=button, double=double, dry_run=False, **filters,
        )

    def click_xy(self, x: int, y: int, *, button: str = "left", double: bool = False):
        return self._oe.click_xy(int(x), int(y), button=button, double=double)

    def send_hotkey(self, combo: str):
        keys = _combo_keys(combo)
        if not keys:
            raise ValueError("hotkey step has no combo")
        return self._oe.send_hotkey(*keys)

    def type_text(self, text: str):
        return self._oe.type_text(text or "")

    def scroll(self, dx: int, dy: int):
        return self._oe.scroll(int(dx), int(dy))

    def drag(self, from_xy: tuple[int, int], to_xy: tuple[int, int]):
        return self._oe.drag(from_xy, to_xy)

    # -- observation --

    def probe(self, spec: dict[str, Any], *, hwnd: int = 0) -> dict[str, Any]:
        """Observe the desktop and report whether ``spec`` holds.

        ``found`` is ``True`` / ``False`` when the state was actually read,
        and ``None`` when this machine cannot read it (no hwnd, no filter) -
        "cannot tell" is never folded into "holds".
        """
        present = bool(spec.get("present", True))
        title = spec.get("window_title_contains")
        if title:
            windows = self._oe.list_windows()
            matched = [
                w for w in windows
                if title.lower() in (getattr(w, "title", "") or "").lower()
            ]
            found = bool(matched)
            sample = f" first={matched[0].title!r}" if matched else ""
            return {
                "found": found if present else not found,
                "observed": (
                    f"windows={len(windows)} title~{title!r} "
                    f"matched={len(matched)}{sample}"
                ),
            }

        filters = spec.get("element") or {}
        if not filters:
            return {"found": None, "observed": "expectation has no window title or element filter"}
        if not hwnd:
            return {"found": None, "observed": "hwnd=0: element tree not addressable"}
        elements = self._oe.detect_elements(int(hwnd))
        matched = self._oe.find_elements(elements, **filters)
        found = bool(matched)
        sample = ""
        if matched:
            first = matched[0]
            sample = f" first={first.control_type}:{first.name!r}"
        return {
            "found": found if present else not found,
            "observed": (
                f"hwnd={hwnd} elements={len(elements)} "
                f"matched={len(matched)}{sample}"
            ),
        }


# ---------------------------------------------------------------------------
# Replayer
# ---------------------------------------------------------------------------

class Replayer:
    """Replay a Trajectory and report whether each action took effect.

    When `dry_run=True` (default), no real input is sent and no expectation
    is judged; actions are reported as ``planned``. Set `dry_run=False` to
    actually actuate and judge.

    ``backend`` is injectable: anything exposing the same methods as
    :class:`OpenEyesBackend`. Tests pass a fake, so no mouse or keyboard is
    ever touched.
    """

    def __init__(self, dry_run: bool = True, fail_on_unknown: bool = False,
                 backend: Any | None = None, retries: int = 2,
                 timeout: float = 2.0, interval: float = 0.1):
        self.dry_run = dry_run
        self.fail_on_unknown = fail_on_unknown
        self.backend = backend
        self.retries = max(1, int(retries))
        self.timeout = max(0.0, float(timeout))
        self.interval = max(0.0, float(interval))

    # -- public --

    def replay(self, trajectory: Trajectory) -> ReplayReport:
        report = ReplayReport(
            trajectory_id=trajectory.id,
            trajectory_name=trajectory.name,
            dry_run=self.dry_run,
        )

        planned: list[tuple[int, Any, dict[str, Any], dict[str, Any] | None, str]] = []
        for index, step in enumerate(trajectory.steps):
            plan = _describe_actuate(step)
            if plan is None:
                report.skipped.append(
                    {"index": index, "reason": "unknown action", "step": step.to_dict()},
                )
                continue
            spec, source = expectation_from_step(step)
            planned.append((index, step, plan, spec, source))

        if self.dry_run:
            # Planning needs no desktop access at all: nothing is sent and
            # nothing is observed, so nothing is claimed.
            for index, step, plan, spec, source in planned:
                report.actions.append({
                    "index": index,
                    "action": step.action,
                    "plan": plan,
                    "status": STATUS_PLANNED,
                    "expectation": spec,
                    "expectation_source": source,
                    "sent": False,
                })
            return report

        backend = self.backend
        if backend is None:
            try:
                backend = OpenEyesBackend(_import_openeyes())
            except Exception as e:
                message = f"openeyes not importable: {e}"
                report.errors.append(message)
                for index, step, plan, spec, source in planned:
                    report.actions.append(
                        self._entry(index, step, plan, spec, source,
                                    status=STATUS_FAILED, error=message),
                    )
                return report

        for index, step, plan, spec, source in planned:
            report.actions.append(self._execute(backend, index, step, plan, spec, source))
        return report

    # -- one step --

    def _execute(self, backend, index: int, step, plan: dict[str, Any],
                 spec: dict[str, Any] | None, source: str) -> dict[str, Any]:
        entry = self._entry(index, step, plan, spec, source)
        ladder = self._ladder(plan)
        if not ladder:
            error = "no structured selector and no bbox center to act on"
            entry["status"] = STATUS_FAILED
            entry["error"] = error
            entry["reason"] = error
            return entry

        if spec is None:
            delivered, error = self._deliver(backend, plan, ladder, entry)
            entry["status"] = STATUS_UNVERIFIED if delivered else STATUS_FAILED
            entry["reason"] = source if delivered else error
            entry["error"] = None if delivered else error
            return entry

        before = self._probe(backend, spec, plan)
        if before.get("found") is None:
            delivered, error = self._deliver(backend, plan, ladder, entry)
            entry["status"] = STATUS_UNVERIFIED if delivered else STATUS_FAILED
            entry["reason"] = (
                f"expectation not observable: {before.get('observed')}"
                if delivered else error
            )
            entry["error"] = None if delivered else error
            entry["last_observed"] = before.get("observed")
            return entry

        # Necessary-but-not-sufficient: the expectation already held before
        # the action, so seeing it afterwards does not prove a change.
        entry["preexisting"] = bool(before.get("found"))
        observed = {"detail": before.get("observed")}

        def expect() -> bool:
            probe = self._probe(backend, spec, plan)
            observed["detail"] = probe.get("observed")
            return bool(probe.get("found"))

        result = act_until(
            lambda: self._act(backend, plan, ladder, entry),
            expect,
            retries=int(spec.get("retries") or len(ladder)),
            timeout=float(spec.get("timeout", self.timeout)),
            interval=float(spec.get("interval", self.interval)),
        )
        entry["attempts"] = result["attempts"]
        entry["elapsed"] = round(float(result["elapsed"]), 3)
        entry["last_observed"] = observed["detail"]
        entry["error"] = result["error"]
        if result["ok"]:
            if entry["preexisting"]:
                # The expectation was already true before we acted, so the
                # observation after the action cannot prove that the action
                # caused it. Keep the delivery, but do not claim effect.
                entry["status"] = STATUS_UNVERIFIED
                entry["reason"] = (
                    "expectation already held before the action; "
                    "no state change was observed"
                )
            else:
                entry["status"] = STATUS_VERIFIED
                entry["reason"] = "expected state observed after the action"
        else:
            entry["status"] = STATUS_FAILED
            entry["reason"] = "expected state not observed within timeout"
        return entry

    def _entry(self, index: int, step, plan: dict[str, Any],
               spec: dict[str, Any] | None, source: str, *,
               status: str = STATUS_UNVERIFIED,
               error: str | None = None) -> dict[str, Any]:
        return {
            "index": index,
            "action": step.action,
            "plan": plan,
            "status": status,
            "expectation": spec,
            "expectation_source": source,
            "path": None,
            "paths": [],
            "attempts": 0,
            "elapsed": 0.0,
            "last_observed": None,
            "preexisting": False,
            "sent": False,
            "text_typed": False,
            "reason": None,
            "error": error,
        }

    # -- strategy ladder --

    def _ladder(self, plan: dict[str, Any]) -> list[str]:
        kind = plan["kind"]
        if kind in ("click", "type"):
            ladder: list[str] = []
            if _selector_filters(plan.get("selector") or "") is not None:
                ladder.append("selector")
            if _bbox_xy(plan.get("element")) is not None:
                ladder.append("bbox")
            return ladder
        if kind == "drag":
            target = plan.get("to") or {}
            if _bbox_xy(plan.get("element")) is None:
                return []
            if not isinstance(target.get("x"), (int, float)):
                return []
            if not isinstance(target.get("y"), (int, float)):
                return []
            return ["drag"]
        if kind == "hotkey":
            return ["hotkey"] if _combo_keys(plan.get("combo") or "") else []
        if kind == "wait":
            return ["wait"]
        if kind == "scroll":
            return ["scroll"]
        return []

    def _act(self, backend, plan: dict[str, Any], ladder: list[str],
             entry: dict[str, Any]) -> str:
        """One attempt: take the next rung of the ladder, record it."""
        path = ladder[min(len(entry["paths"]), len(ladder) - 1)]
        entry["paths"].append(path)
        entry["path"] = path
        entry["attempts"] = len(entry["paths"])
        if plan["kind"] == "type" and entry["text_typed"]:
            # Typing is not idempotent: a second type_text would append the
            # same characters again. Re-focus and keep polling instead.
            self._focus(backend, plan, path)
        else:
            self._send(backend, plan, path)
            if plan["kind"] == "type":
                entry["text_typed"] = True
        entry["sent"] = True
        return path

    def _deliver(self, backend, plan: dict[str, Any], ladder: list[str],
                 entry: dict[str, Any]) -> tuple[bool, str | None]:
        """Deliver without an expectation: walk the ladder once, no repeats."""
        last_error: str | None = None
        for path in ladder:
            entry["paths"].append(path)
            entry["path"] = path
            entry["attempts"] = len(entry["paths"])
            try:
                self._send(backend, plan, path)
            except Exception as e:
                last_error = f"{path}: {type(e).__name__}: {e}"
                continue
            entry["sent"] = True
            if plan["kind"] == "type":
                entry["text_typed"] = True
            return True, None
        return False, last_error

    def _focus(self, backend, plan: dict[str, Any], path: str) -> None:
        if path == "selector":
            backend.click_selector(plan["hwnd"], plan["selector"])
        else:
            xy = _bbox_xy(plan.get("element"))
            if xy is None:
                raise ValueError("no bbox center")
            backend.click_xy(xy[0], xy[1])

    def _send(self, backend, plan: dict[str, Any], path: str) -> None:
        kind = plan["kind"]
        if kind == "hotkey":
            backend.send_hotkey(plan["combo"])
        elif kind == "wait":
            time.sleep((plan.get("ms") or 500) / 1000.0)
        elif kind == "click":
            if path == "selector":
                backend.click_selector(
                    plan["hwnd"], plan["selector"],
                    button=plan.get("button", "left"),
                    double=bool(plan.get("double", False)),
                )
            elif path == "bbox":
                xy = _bbox_xy(plan.get("element"))
                if xy is None:
                    raise ValueError("no bbox center")
                backend.click_xy(
                    xy[0], xy[1],
                    button=plan.get("button", "left"),
                    double=bool(plan.get("double", False)),
                )
            else:
                raise ValueError(f"unknown click path: {path}")
        elif kind == "type":
            self._focus(backend, plan, path)
            backend.type_text(plan.get("text") or "")
        elif kind == "scroll":
            backend.scroll(int(plan.get("dx", 0)), int(plan.get("dy", 0)))
        elif kind == "drag":
            xy = _bbox_xy(plan.get("element"))
            to = plan.get("to") or {}
            if xy is None:
                raise ValueError("drag has no source center")
            backend.drag(xy, (int(to["x"]), int(to["y"])))
        else:
            raise ValueError(f"unknown plan kind: {kind}")

    def _probe(self, backend, spec: dict[str, Any],
               plan: dict[str, Any]) -> dict[str, Any]:
        try:
            result = backend.probe(spec, hwnd=int(plan.get("hwnd") or 0))
        except Exception as e:
            return {"found": None,
                    "observed": f"probe error: {type(e).__name__}: {e}"}
        if not isinstance(result, dict):
            return {"found": None, "observed": f"probe returned {type(result).__name__}"}
        found = result.get("found")
        if found not in (True, False, None):
            return {"found": None, "observed": f"probe returned found={found!r}"}
        return {"found": found, "observed": result.get("observed")}
