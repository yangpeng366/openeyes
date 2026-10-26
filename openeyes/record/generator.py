"""Prompt-based trajectory generator.

Two backends:
1. Real: call any OpenAI-compatible chat completion endpoint.
   Configure via env vars:
       LLM_BASE_URL   e.g. https://api.openai.com/v1  (default)
       LLM_API_KEY    bearer token
       LLM_MODEL      e.g. gpt-4o-mini               (default)
2. Mock: deterministic template-based generator that picks plausible
   steps from the element inventory. Used when no LLM is configured
   (or for offline CI / unit tests).

Both backends produce the same Trajectory object, which can then be
fed into the existing replay + export pipeline.
"""
from __future__ import annotations
import json
import os
import time
import uuid
from typing import List, Dict, Any, Optional
from urllib.parse import urlsplit

from .event import UIAEvent, InputEvent, Trajectory
from .prompt_template import render_system, render_user
from .recorder import _build_selector
from openeyes.decision import (
    DecisionBand,
    FakeJevScorer,
    JevScorer,
    ThresholdConfig,
    TypesafeJevScorer,
    classify_confidence,
)


class LLMUnavailable(RuntimeError):
    """Raised when the user wants the real backend but no credentials are set."""


def _call_llm(messages: List[Dict[str, str]], *, model: Optional[str] = None,
              base_url: Optional[str] = None, api_key: Optional[str] = None,
              temperature: float = 0.2, timeout: float = 60.0) -> str:
    """Call an OpenAI-compatible chat completions endpoint.

    Tries the official `openai` package first; falls back to raw urllib so
    we don't introduce a hard dependency on it.
    """
    base = (base_url or os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    key = api_key or os.environ.get("LLM_API_KEY")
    model = model or os.environ.get("LLM_MODEL") or "gpt-4o-mini"
    if not key:
        raise LLMUnavailable(
            "LLM_API_KEY not set. Falling back to mock backend."
        )
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    # Try the openai package first (cleaner timeouts / retries).
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=key, base_url=base, timeout=timeout)
        resp = client.chat.completions.create(**payload)
        return resp.choices[0].message.content or ""
    except ImportError:
        pass
    # Fallback: urllib POST.
    import urllib.request
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - user-supplied endpoint
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"] or ""


# ---- Public API ----


def generate_trajectory_from_prompt(
    description: str,
    *,
    app_name: str = "",
    element_inventory: Optional[List[Dict[str, Any]]] = None,
    few_shot_examples: Optional[List[str]] = None,
    backend: str = "auto",
    jev_scorer: Optional[JevScorer] = None,
    jev_thresholds: Optional[ThresholdConfig] = None,
) -> Trajectory:
    """Generate a Trajectory from a natural-language description.

    `backend`:
        - "auto": real LLM if LLM_API_KEY is set, else mock.
        - "real": force real LLM (raise LLMUnavailable if no key).
        - "mock": always deterministic template generator.
        - "jev": select an inventory element with JEV. Uses the real
          System One wire when TYPESAFE_API_KEY is configured, or when
          TYPESAFE_BASE_URL points at a loopback node (a local XERJ node
          may answer keyless); otherwise uses the deterministic fake scorer.
    """
    inv = element_inventory or []
    if backend == "real":
        return _generate_real(description, app_name, inv, few_shot_examples)
    if backend == "mock":
        return _generate_mock(description, app_name, inv, few_shot_examples)
    if backend == "jev":
        scorer = jev_scorer or _create_jev_scorer()
        return _generate_jev(
            description,
            app_name,
            inv,
            scorer,
            jev_thresholds or ThresholdConfig(),
        )
    # auto
    try:
        return _generate_real(description, app_name, inv, few_shot_examples)
    except LLMUnavailable:
        return _generate_mock(description, app_name, inv, few_shot_examples)


def _is_local_base_url(base_url: str) -> bool:
    """True for a loopback System One endpoint (a local XERJ node)."""
    if not base_url:
        return False
    host = urlsplit(base_url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


def _create_jev_scorer() -> JevScorer:
    """Pick the scorer that can actually answer.

    A hosted Jev endpoint needs `TYPESAFE_API_KEY`; a local XERJ node speaks
    the same wire and may run keyless, so a loopback `TYPESAFE_BASE_URL` is
    enough to opt into the real scorer.
    """
    api_key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    base_url = (os.environ.get("TYPESAFE_BASE_URL") or "").strip()
    if api_key or _is_local_base_url(base_url):
        return TypesafeJevScorer(api_key, base_url=base_url or None)
    return FakeJevScorer()


def _empty_trajectory(description: str, app_name: str, suffix: str) -> Trajectory:
    trajectory = Trajectory.new(
        name=description[:40] or suffix,
        app_name=app_name,
        description=description,
    )
    trajectory.close()
    return trajectory


def _generate_jev(
    description: str,
    app_name: str,
    inv: List[Dict[str, Any]],
    scorer: JevScorer,
    thresholds: ThresholdConfig,
) -> Trajectory:
    """Generate a one-step trajectory after JEV validates the target."""
    candidates = [element for element in inv if element.get("interactive", True)]
    if not candidates:
        return _empty_trajectory(description, app_name, "jev")

    labels = [_jev_candidate_label(index, element)
              for index, element in enumerate(candidates)]
    score = scorer.ask(description, labels, app_name=app_name)
    band = classify_confidence(score.confidence, score.prob, thresholds)
    if score.target_index < 0 or band not in {DecisionBand.ACT, DecisionBand.FLAG_FOR_REVIEW}:
        return _empty_trajectory(description, app_name, "jev")

    element = candidates[score.target_index]
    trajectory = Trajectory.new(
        name=description[:40] or "jev",
        app_name=app_name,
        description=description,
    )
    trajectory.add(UIAEvent(
        ts=time.time(),
        action="click",
        element=element,
        selector=_build_selector(element),
        app_name=app_name,
        intent=description,
        meta={
            "decision_backend": type(scorer).__name__,
            "decision_band": band.value,
            "jev_confidence": score.confidence,
            "jev_probability": score.prob,
            "jev_latency_ms": score.latency_ms,
            "requires_review": band == DecisionBand.FLAG_FOR_REVIEW,
            "thresholds": {
                "low_stakes": thresholds.low_stakes_threshold,
                "high_stakes": thresholds.high_stakes_threshold,
                "uncertain_low": thresholds.uncertain_low,
                "uncertain_high": thresholds.uncertain_high,
            },
        },
    ))
    trajectory.close()
    return trajectory


def _jev_candidate_label(index: int, element: Dict[str, Any]) -> str:
    control_type = element.get("control_type") or "Element"
    name = element.get("name") or f"candidate {index}"
    automation_id = element.get("automation_id")
    suffix = f" (automation_id={automation_id})" if automation_id else ""
    return f"{control_type}: {name}{suffix}"


# ---- Real backend ----


def _generate_real(
    description: str,
    app_name: str,
    inv: List[Dict[str, Any]],
    few_shot: Optional[List[str]],
) -> Trajectory:
    msgs = [
        {"role": "system", "content": render_system()},
        {"role": "user", "content": render_user(description, app_name, inv, few_shot)},
    ]
    raw = _call_llm(msgs).strip()
    # Strip markdown fences if the model added them.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3]
    return _parse_jsonl_response(raw, app_name=app_name, description=description)


def _parse_jsonl_response(raw: str, *, app_name: str, description: str) -> Trajectory:
    """Parse the model's JSONL output into a Trajectory. Tolerant of stray
    prose lines.
    """
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    header_idx = None
    for i, ln in enumerate(lines):
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("_kind") == "header":
            header_idx = i
            break
    if header_idx is None:
        # Construct a header from the first JSON object if it's a step; else
        # synthesize one.
        header_idx = 0
    traj = Trajectory.new(name=description[:40] or "generated", app_name=app_name, description=description)
    for ln in lines[header_idx + 1:]:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if "element" in obj:
            traj.steps.append(UIAEvent(
                ts=obj.get("ts", time.time()),
                action=obj.get("action", "click"),
                element=obj.get("element", {}),
                selector=obj.get("selector") or _build_selector(obj.get("element", {})),
                app_name=obj.get("app_name", app_name),
                window_title=obj.get("window_title", ""),
                hwnd=int(obj.get("hwnd", 0)),
                pid=int(obj.get("pid", 0)),
                intent=obj.get("intent", ""),
                input_text=obj.get("input_text"),
                meta=obj.get("meta", {}) or {},
            ))
        else:
            traj.steps.append(InputEvent(
                ts=obj.get("ts", time.time()),
                action=obj.get("action", "hotkey"),
                combo=obj.get("combo"),
                dx=obj.get("dx"),
                dy=obj.get("dy"),
                duration_ms=obj.get("duration_ms"),
                intent=obj.get("intent", ""),
                meta=obj.get("meta", {}) or {},
            ))
    traj.close()
    return traj


# ---- Mock backend ----


# A small bag of plausible "templates" - if the model is unavailable, we
# pick one that roughly matches the description keyword. This is enough for
# CI and demo purposes; the real value is the real backend.

_MOCK_TEMPLATES = [
    {
        "match": lambda d: any(k in d.lower() for k in ["rotate", "server", "vpn", "switch"]),
        "steps": [
            # (action, ctype, name, aid, text_or_duration, combo)
            ("click",  "Button",   "Select server",        "ServerListToggle", None,    None),
            ("wait",   None,      None,                  None,               400,    None),
            ("type",   "Edit",     "Filter servers",      "ServerFilter",     "Hong Kong", None),
            ("click",  "ListItem", "Hong Kong - 03",      "",                 None,    None),
            ("hotkey", None,      None,                  None,               None,    "ctrl+enter"),
            ("wait",   None,      None,                  None,               1500,   None),
            ("click",  "Button",   "Secure my connection","SecureButton",     None,    None),
        ],
    },
    {
        "match": lambda d: any(k in d.lower() for k in ["search", "find", "lookup"]),
        "steps": [
            ("click",  "Edit", "Search box", "SearchInput", None,  None),
            ("type",   "Edit", "Search box", "SearchInput", "openeyes", None),
            ("hotkey", None,  None,         None,         None,  "Enter"),
            ("wait",   None,  None,         None,         800,   None),
        ],
    },
    {
        "match": lambda d: any(k in d.lower() for k in ["open", "launch", "start"]),
        "steps": [
            ("hotkey", None, None,         None,         None,  "ctrl+o"),
            ("wait",   None, None,         None,         500,   None),
            ("type",   "Edit", "Open path","PathInput",  "{path}", None),
            ("hotkey", None,  None,        None,         None,  "Enter"),
        ],
    },
]

_DEFAULT_TEMPLATE = {
    "match": lambda d: True,
    "steps": [
        ("click", "Button", "Start",   "StartButton",   None, None),
        ("wait",  None,    None,      None,            500,  None),
        ("click", "Button", "Confirm", "ConfirmButton", None, None),
    ],
}


def _generate_mock(
    description: str,
    app_name: str,
    inv: List[Dict[str, Any]],
    few_shot: Optional[List[str]],
) -> Trajectory:
    """Deterministic fallback that maps keywords to plausible step sequences.

    If an inventory is supplied, we also try to pick automation_ids that
    match the requested step's element name (best-effort).
    """
    inv_by_name = {el.get("name", "").lower(): el for el in inv}
    tpl = next((t for t in _MOCK_TEMPLATES if t["match"](description)), _DEFAULT_TEMPLATE)
    traj = Trajectory.new(name=description[:40] or "mock", app_name=app_name, description=description)
    for step in tpl["steps"]:
        # step tuple layout (uniform for every action):
        #   (action, ctype, name, aid, text_or_duration, combo)
        # Use None for unused slots so position is unambiguous.
        action, ctype, name, aid, slot5, combo = step
        if action == "wait":
            traj.add(InputEvent(ts=time.time(), action="wait", duration_ms=slot5,
                                intent="Let the previous action settle"))
            continue
        if action == "hotkey":
            traj.add(InputEvent(ts=time.time(), action="hotkey", combo=combo,
                                intent="Apply / confirm"))
            continue
        text = slot5
        # Try to upgrade with a real element from the inventory.
        real = inv_by_name.get(name.lower(), {})
        if real:
            ed = real
            aid = real.get("automation_id") or aid
        else:
            ed = {
                "backend": "uia",
                "control_type": ctype,
                "name": name,
                "automation_id": aid or "",
                "class_name": "",
                "bbox": {"x": 0, "y": 0, "w": 200, "h": 30},
                "center": {"x": 100, "y": 15},
                "score": 1.0,
                "hint": None,
                "interactive": True,
                "state": {"enabled": True, "visible": True, "focused": False, "selected": False},
                "parent_chain": [],
            }
        ev = UIAEvent(
            ts=time.time(),
            action=action,
            element=ed,
            selector=_build_selector(ed),
            app_name=app_name,
            intent=f"{action} on {ctype} '{name}'",
            input_text=text,
        )
        traj.add(ev)
    traj.close()
    return traj
