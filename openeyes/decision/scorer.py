"""Scorer interface + FakeJevScorer (no API key, deterministic).

The real `JevScorer` calls `https://api.typesafe.ai/v1/systemone` and is
intentionally **not implemented in this layer**. ACH
(`com.agentcloud.scorer.JevAsker`) owns the canonical implementation;
OpenEyes consumers that want a real scorer should either:

  (a) reuse ACH via subprocess / HTTP gateway, or
  (b) call the TypeSafe endpoint directly with the protocol documented at
      https://docs.typesafe.ai/llms.txt (see `JEV_OFFICIAL_SKILL_ABSORPTION.md`).

This module exposes the protocol surface only: a `JevScorer` ABC + a
deterministic `FakeJevScorer` for unit tests. Wire-up of a real client
is left to the consumer (mirrors ACH's choice to keep the real client out
of `fast-jev-compaction`).

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
from abc import ABC, abstractmethod
from dataclasses import dataclass


def validate_probabilities(probabilities, choice, candidate_ids):
    """Enforce the Jev response contract (mirrors jev-ultrafast `validate_choice`).

    Rules:
    1. `choice` must be one of `candidate_ids`.
    2. `probabilities` keys must cover `candidate_ids` exactly (set equality).
    3. Every value must be finite and in [0, 1].
    4. Sum of probabilities must be approximately 1 (tolerance 0.02).
    5. The chosen key must equal the top-1 probability (tolerance 1e-6).

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
    if abs(s - 1.0) >= 0.02:
        raise ValueError(f"sum(probabilities) = {s:.4f}, must be within 0.02 of 1.0")
    top = max(nums)
    if float(probabilities[choice]) < top - 1e-6:
        raise ValueError(f"choice {choice!r} ({probabilities[choice]}) is not top-1 (max={top})")

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
        prob_floor = (1.0 / n) + 0.01 if n > 0 else 0.0
        return self._build(0, 0.55, prob_floor, candidates)