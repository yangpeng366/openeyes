"""Scorer interface + FakeJevScorer (no API key, deterministic).

The real `JevScorer` calls `https://api.typesafe.ai/v1/systemone` (override
the host with `TYPESAFE_BASE_URL`; a local XERJ node speaks the same wire on
its native REST port, e.g. `http://127.0.0.1:9201/v1`, and needs no hosted
key). ACH (`com.agentcloud.scorer.JevAsker`) owns the canonical hosted
implementation; this module keeps the wire contract locally so the decision
layer can also run against a keyless local node.

The protocol surface is a `JevScorer` ABC + a deterministic
`FakeJevScorer` for unit tests, plus `TypesafeJevScorer` for the real wire.

Why FakeJevScorer matters:
- Per `Jev-OpenEyes-experience.md` §3, the fake Jev contract test
  template is the single most useful artifact: it lets us verify the
  decision layer routing without spending a TYPESAFE_API_KEY credit.
- Per `.tmp/Jev-OpenEyes-experience.md` §4.E, fake scorers must handle
  null/empty state with `Boolean.TRUE.equals(obj)` rather than direct
  casts; in Python this is `(state.get(key) is True)` rather than
  `state[key] is True` to avoid KeyError on missing keys.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

from openeyes.decision.scale import SCALE_PROBABILITY, SCALE_SUPPORT_SHARE


def validate_probabilities(probabilities, choice, candidate_ids, *, require_distribution=True):
    """Enforce the Jev response contract (mirrors jev-ultrafast `validate_choice`).

    Rules:
    1. `choice` must be one of `candidate_ids`.
    2. `probabilities` keys must cover `candidate_ids` exactly (set equality).
    3. Every value must be finite and in [0, 1].
    4. Sum of probabilities must be approximately 1 (tolerance 0.02).
    5. The chosen key must equal the top-1 probability (tolerance 1e-6).

    `require_distribution=False` drops rule 4 only. The hosted Jev API
    returns a distribution, but a local XERJ history vote returns per-option
    *support* scores (the winner's share of the labelled neighbours, losers
    at 0.0), which sum to well under 1. Rules 1, 2, 3 and 5 still hold there.

    Raises `ValueError` on any violation. Returns None on success.

    Shared by FakeJevScorer and any real JevScorer implementation so the
    contract is enforced at exactly one boundary.
    """
    import math
    ids_set = set(candidate_ids)
    prob_set = set(probabilities.keys())
    if choice not in ids_set:
        raise ValueError(f"choice {choice!r} not in candidate_ids {candidate_ids!r}")
    if prob_set != ids_set:
        raise ValueError(f"probabilities keys {prob_set} != candidate_ids {ids_set}")
    nums = []
    for k, v in probabilities.items():
        if not isinstance(v, (int, float)) or not math.isfinite(v) or not (0.0 <= float(v) <= 1.0):
            raise ValueError(f"probabilities[{k!r}] = {v!r} not finite in [0,1]")
        nums.append(float(v))
    s = sum(nums)
    if require_distribution and abs(s - 1.0) >= 0.02:
        raise ValueError(f"sum(probabilities) = {s:.4f}, must be within 0.02 of 1.0")
    top = max(nums)
    if float(probabilities[choice]) < top - 1e-6:
        raise ValueError(f"choice {choice!r} ({probabilities[choice]}) is not top-1 (max={top})")

def is_loopback_url(url: str) -> bool:
    """True for a URL served by this machine."""
    host = urlsplit(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


def _error_reason(exc: urllib.error.HTTPError) -> str:
    """Best-effort human reason out of an error response body.

    A local XERJ node answers 422/503 with `{"error": {"type", "reason"}}`;
    the hosted API answers with its own shape. Both are reduced to one line
    so the caller sees what actually went wrong.
    """
    try:
        raw = exc.read().decode("utf-8", "replace").strip()
    except Exception:
        return str(exc.reason or "")
    if not raw:
        return str(exc.reason or "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:400]
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, dict):
        return str(error.get("reason") or error.get("type") or raw[:400])
    return raw[:400]


class JevAskError(RuntimeError):
    """Real scorer transport / parse failures. Caller falls back."""


@dataclass(frozen=True)
class JevScore:
    """Result of one Jev ask.

    `probabilities` is the full per-candidate probability dict (real Jev
    always provides it; FakeJevScorer may omit). When present, callers
    can run `validate_probabilities()` to enforce the Jev response
    contract (see jev-ultrafast `validate_choice`).
    """
    target_index: int          # -1 for "none of the above"
    confidence: float          # in [0, 1]
    prob: float                # top-1 probability in [0, 1]
    latency_ms: int
    raw: dict | None = None
    probabilities: frozenset | None = None  # immutable view of dict; real Jev uses dict, fake omits
    # Which scale `confidence`/`prob` are on: "probability" (hosted judge) or
    # "support_share" (a local history vote). The thresholds belong to the
    # scale, not to the scorer - see openeyes.decision.scale.
    scale: str = SCALE_PROBABILITY

    def __post_init__(self):
        for name in ("confidence", "prob"):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} out of [0,1]: {v}")
        if self.probabilities is not None:
            # Normalize dict -> frozenset of (key, value) for hashability.
            if not isinstance(self.probabilities, (dict, frozenset)):
                raise TypeError("probabilities must be dict or frozenset of (key, value)")
            if isinstance(self.probabilities, dict):
                # frozenset(tuple) — frozen-friendly
                object.__setattr__(self, "probabilities",
                                   frozenset(self.probabilities.items()))


class JevScorer(ABC):
    """Protocol surface for any Jev-compatible scorer."""

    # How the caller should name its candidates. "descriptive" lets it
    # decorate a name ("Button: 消息 (automation_id=…)"); "name" means the
    # plain option name only. A local history vote retrieves neighbours by
    # the question text, so it only answers names it has labels for.
    candidate_key_style: str = "descriptive"

    @abstractmethod
    def ask(self, goal: str, candidates: list[str], *, app_name: str = "") -> JevScore:
        """Pick one candidate (or `-1` for none-of-the-above) with confidence + prob."""


class FakeJevScorer(JevScorer):
    """Deterministic scorer for tests. No network.

    Routing rules (mirrors ACH `FakeJevAsker` from
    `JevDecisionTreeTest.java`, but with null-safe semantics per
    `.tmp/Jev-OpenEyes-experience.md` §4.E):

    - empty candidates -> JevScore(target_index=-1, confidence=0.50, prob=0.50)
    - candidate containing the goal (case-insensitive substring) ->
      target_index of first match, confidence=0.95, prob=0.90
    - candidate starting with the goal (case-insensitive prefix) ->
      confidence=0.80, prob=0.70
    - otherwise -> first candidate, confidence=0.55, prob=0.40

    The scorer **always returns the full probabilities dict** and runs
    `validate_probabilities()` on it before returning — so any rule
    change that breaks the contract fails the test immediately.

    Override routing via `rules` dict (test injection point).
    Special rule keys:
      - `always_pick_none: True` -> target_index=-1, conf=0.95
      - `inject_probabilities: dict` -> use this probabilities map
        (used to test contract-violation cases that FakeJevScorer
        would otherwise never produce).
    """

    def __init__(self, *, rules: dict | None = None, latency_ms: int = 0):
        self.rules = rules or {}
        self.latency_ms = latency_ms

    def _build(self, target_index: int, confidence: float, prob: float,
               candidates: list[str]) -> JevScore:
        """Construct full probabilities dict + run contract validation.

        Probabilities are normalized so the dict sums to exactly 1.0:
        - target gets `prob`
        - remaining (1 - prob) is split uniformly across the other n-1 candidates
        - when n == 1, the single candidate owns the full mass (1.0)
        - when prob is None / 0 / negative, fall back to uniform distribution
        """
        n = len(candidates)
        if n == 0:
            probs: dict[str, float] = {}
        else:
            if target_index < 0:
                # picked_none: distribute uniformly
                share = 1.0 / n
                probs = {str(i): share for i in range(n)}
            else:
                prob_top = float(prob) if prob and prob > 0 else 0.0
                prob_top = max(0.0, min(1.0, prob_top))
                if n == 1:
                    probs = {str(target_index): 1.0}
                else:
                    remaining = 1.0 - prob_top
                    share = remaining / (n - 1)
                    probs = {}
                    for i in range(n):
                        probs[str(i)] = prob_top if i == target_index else share
        choice_key = str(target_index) if target_index >= 0 else "__none__"
        if target_index >= 0:
            validate_probabilities(probs, choice_key, [str(i) for i in range(n)])
        return JevScore(target_index=target_index,
                        confidence=confidence, prob=prob,
                        latency_ms=self.latency_ms, probabilities=probs)

    def ask(self, goal: str, candidates: list[str], *, app_name: str = "") -> JevScore:
        # Null-safe per .tmp/Jev-OpenEyes-experience.md §4.E:
        # Python analog of `Boolean.TRUE.equals(state.get(key))`.
        goal_lc = (goal or "").lower().strip()
        if not goal_lc or not candidates:
            return self._build(-1, 0.50, 0.50, candidates)

        # Test injection: contract-violation case
        if "inject_probabilities" in self.rules:
            probs = dict(self.rules["inject_probabilities"])
            choice = self.rules.get("inject_choice", max(probs, key=probs.get))
            validate_probabilities(probs, choice, list(probs.keys()))
            return JevScore(target_index=int(choice), confidence=0.95,
                            prob=probs[choice], latency_ms=self.latency_ms,
                            probabilities=probs)

        # Test injection point
        if self.rules.get("always_pick_none") is True:
            return self._build(-1, 0.95, 0.95, candidates)

        for i, c in enumerate(candidates):
            if goal_lc in (c or "").lower():
                return self._build(i, 0.95, 0.90, candidates)
        for i, c in enumerate(candidates):
            if (c or "").lower().startswith(goal_lc):
                return self._build(i, 0.80, 0.70, candidates)
        # Default fallback: low confidence + uniform-ish probabilities
        # (just above 1/n so top-1 == choice under the contract).
        n = len(candidates)
        prob_floor = 1.0 if n == 1 else ((1.0 / n) + 0.01 if n > 0 else 0.0)
        return self._build(0, 0.55, prob_floor, candidates)


def _criteria_for(candidates: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    """Build the System One ``criteria`` map plus a label -> candidate index map.

    The System One protocol names each option by the KEY of ``criteria`` —
    upstream examples read ``{"spam": "prize claims...", "ham": "..."}`` — so
    the candidate names are the keys. A local XERJ node votes over a labelled
    history whose labels are candidate names, and that only lines up when the
    keys are the names; index keys stay the fallback for empty or duplicated
    candidate names.
    """
    names = [candidate or "" for candidate in candidates]
    if all(names) and len(set(names)) == len(names):
        labels = list(names)
    else:
        labels = [str(index) for index in range(len(candidates))]
    criteria = {label: names[index] or label for index, label in enumerate(labels)}
    return criteria, {label: index for index, label in enumerate(labels)}


class TypesafeJevScorer(JevScorer):
    """Real Jev scorer backed by the TypeSafe ``choice`` protocol.

    The same wire is served by a local XERJ node on its native REST port
    (`http://127.0.0.1:9201/v1/systemone`); such a node may run keyless
    (`--insecure`) or with its own admin key, so an empty key is allowed
    and simply omits the Authorization header.
    """

    @property
    def candidate_key_style(self) -> str:
        """A local node answers with the option names its history knows, so
        the candidates have to be named plainly; the hosted judge takes any
        key, so a descriptive one is richer there."""
        return "name" if is_loopback_url(self.base_url) else "descriptive"

    def __init__(
        self,
        api_key: str = "",
        *,
        base_url: str | None = None,
        model: str = "jev-latest",
        timeout: float = 25.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (
            base_url
            or os.environ.get("TYPESAFE_BASE_URL")
            or "https://api.typesafe.ai"
        ).rstrip("/")
        self.model = model
        self.timeout = timeout

    def _url(self) -> str:
        if self.base_url.endswith("/systemone"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/systemone"
        return f"{self.base_url}/v1/systemone"

    def _request(self, goal: str, candidates: list[str], *, app_name: str) -> dict:
        criteria, label_to_index = _criteria_for(candidates)
        payload = {
            "model": self.model,
            "state": {"app_name": app_name, "goal": goal},
            "questions": {
                "target": {
                    "type": "choice",
                    "instructions": (
                        "Select the single UI element that best matches the goal. "
                        "Return none only if no candidate can advance the goal."
                    ),
                    "criteria": criteria,
                }
            },
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self._url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise JevAskError(
                f"Jev request failed with HTTP {exc.code}: {_error_reason(exc)}"
            ) from exc
        except Exception as exc:
            raise JevAskError(f"TypeSafe Jev request failed: {exc}") from exc
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        try:
            return {
                "body": json.loads(body),
                "latency_ms": latency_ms,
                "label_to_index": label_to_index,
            }
        except json.JSONDecodeError as exc:
            raise JevAskError("TypeSafe Jev returned malformed JSON") from exc

    def ask(self, goal: str, candidates: list[str], *, app_name: str = "") -> JevScore:
        result = self._request(goal, candidates, app_name=app_name)
        label_to_index = result["label_to_index"]
        body = result["body"]
        answer = body.get("answers", {}).get("target")
        if not isinstance(answer, dict):
            raise JevAskError("TypeSafe Jev response is missing answers.target")
        choice = answer.get("choice")
        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")
        if not isinstance(choice, str) or not isinstance(probabilities, dict):
            raise JevAskError("TypeSafe Jev choice answer is malformed")
        # A local XERJ history vote answers with per-option support scores
        # (the winner's share of the labelled neighbours, losers at 0.0)
        # instead of a probability distribution, and echoes its own model
        # name instead of a Jev model name.
        support_scores = (
            body.get("model") == "xerj-history-vote-1" or "decisions" in body
        )
        try:
            confidence_value = float(confidence)
            if support_scores:
                probabilities = {
                    label: float(probabilities.get(label, 0.0))
                    for label in label_to_index
                }
            validate_probabilities(
                probabilities,
                choice,
                list(label_to_index),
                require_distribution=not support_scores,
            )
            target_index = label_to_index.get(choice)
            if target_index is None:
                raise JevAskError(f"TypeSafe Jev choice {choice!r} is not a known candidate")
            by_index = {
                str(index): probabilities[label] for label, index in label_to_index.items()
            }
            return JevScore(
                target_index=target_index,
                confidence=confidence_value,
                prob=float(probabilities[choice]),
                latency_ms=result["latency_ms"],
                raw=answer,
                probabilities=by_index,
                scale=SCALE_SUPPORT_SHARE if support_scores else SCALE_PROBABILITY,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise JevAskError(f"TypeSafe Jev choice answer is invalid: {exc}") from exc
