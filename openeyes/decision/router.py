"""decide() — the single public entry point for the decision layer.

Behavior contract (parity with ACH FEAT-03):
- enabled=False: return `None` (caller bypasses the layer entirely).
- enabled=True + empty elements: return Action(BLOCKED).
- enabled=True + threshold ACT: return Action(CLICK/DONE/etc.) with target_index.
- enabled=True + FLAG_FOR_REVIEW: return Action with reason="flag_for_review".
- enabled=True + FALLBACK: return Action(BLOCKED, reason="fallback").
- enabled=True + UNCERTAIN: return Action(BLOCKED, reason="uncertain").

Caller is expected to:
  action = decide(elements=..., goal="...", enabled=True, scorer=...)
  if action is None:
      # decision layer disabled — use scripted path
  elif action.kind == ActionKind.BLOCKED:
      # stop the task loop
  else:
      # resolve action.target_index -> openeyes.Element -> click/type/...
"""
from __future__ import annotations

from dataclasses import dataclass

from openeyes.decision.action import Action, ActionKind
from openeyes.decision.scale import thresholds_for_scale
from openeyes.decision.scorer import FakeJevScorer, JevAskError, JevScorer
from openeyes.decision.threshold import DecisionBand, ThresholdConfig, classify_confidence


@dataclass
class DecisionContext:
    enabled: bool = False
    cfg: ThresholdConfig | None = None
    scorer: JevScorer | None = None
    app_name: str = ""

    def __post_init__(self):
        # `cfg` stays None on purpose: the thresholds belong to the scale the
        # answer is on, and only the answer knows that. An explicitly given
        # config still wins.
        if self.enabled and self.scorer is None:
            # Default to FakeJevScorer for safety: enabled=True must never
            # auto-call the network. Caller must explicitly pass a real
            # JevScorer to use real Jev.
            self.scorer = FakeJevScorer()


def decide(elements, goal: str, ctx: DecisionContext) -> Action | None:
    """Backend-agnostic decision.

    `elements` may be a list of `openeyes.Element` (preferred) or anything
    with `__len__` and `__getitem__`. The caller resolves the resulting
    `Action.target_index` into a backend-specific call.
    """
    if not ctx.enabled:
        return None

    if not elements:
        return Action(kind=ActionKind.BLOCKED, reason="no_elements")

    scorer = ctx.scorer or FakeJevScorer()

    candidates = [(e.name or "") for e in elements]
    try:
        score = scorer.ask(goal=goal, candidates=candidates, app_name=ctx.app_name)
    except JevAskError as e:
        return Action(kind=ActionKind.BLOCKED, reason=f"jev_ask_error: {e}")

    cfg = ctx.cfg or thresholds_for_scale(score.scale)
    band = classify_confidence(score.confidence, score.prob, cfg)

    if score.target_index == -1:
        return Action(kind=ActionKind.BLOCKED, reason="picked_none")
    if not (0 <= score.target_index < len(elements)):
        return Action(kind=ActionKind.BLOCKED, reason="invalid_target")

    if band == DecisionBand.FALLBACK:
        return Action(kind=ActionKind.BLOCKED, reason=f"fallback(conf={score.confidence:.2f})")
    if band == DecisionBand.UNCERTAIN:
        return Action(kind=ActionKind.BLOCKED,
                      reason=f"uncertain(conf={score.confidence:.2f}, prob={score.prob:.2f})")

    if band == DecisionBand.FLAG_FOR_REVIEW:
        return Action(kind=ActionKind.CLICK, target_index=score.target_index,
                      reason=f"flag_for_review(conf={score.confidence:.2f})")
    # ACT
    return Action(kind=ActionKind.CLICK, target_index=score.target_index,
                  reason=f"act(conf={score.confidence:.2f}, prob={score.prob:.2f})")