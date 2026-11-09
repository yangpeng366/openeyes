"""Attention-budget signal on Action: one bit that says "a human should look".

The decision layer only reports the band's verdict; it never reaches into
the agent. Whoever spends a human's attention (an agent's own cursor, an
interrupt) consumes `Action.needs_attention`.
"""
from __future__ import annotations

from openeyes.core.schema import BBox, Center, Element
from openeyes.decision import (
    Action,
    ActionKind,
    DecisionContext,
    JevAskError,
    JevScore,
    JevScorer,
    decide,
)


def _make_element(name: str, control_type: str = "Button") -> Element:
    return Element(
        backend="uia", control_type=control_type, name=name,
        bbox=BBox(x=0, y=0, w=10, h=10), center=Center(x=5, y=5),
    )


class _FixedScorer(JevScorer):
    """A scorer with a hand-picked verdict, so every band is reachable.

    FakeJevScorer on its own only reaches ACT and UNCERTAIN; the band under
    test is named here instead of relying on the fake's routing rules.
    """

    def __init__(self, confidence: float, prob: float, target_index: int = 0):
        self.confidence = confidence
        self.prob = prob
        self.target_index = target_index

    def ask(self, goal, candidates, *, app_name=""):
        return JevScore(target_index=self.target_index, confidence=self.confidence,
                        prob=self.prob, latency_ms=0)


class _ErrorScorer(JevScorer):
    def ask(self, goal, candidates, *, app_name=""):
        raise JevAskError("simulated 503")


# ---------- the signal on its own ----------

def test_action_defaults_to_no_attention():
    """An Action nobody flagged does not ask for a human."""
    a = Action(kind=ActionKind.CLICK, target_index=0)
    assert a.needs_attention is False


def test_action_to_dict_carries_the_signal():
    a = Action(kind=ActionKind.CLICK, target_index=0, needs_attention=True)
    assert a.to_dict()["needs_attention"] is True
    assert Action(kind=ActionKind.CLICK, target_index=0).to_dict()["needs_attention"] is False


# ---------- the router decides who gets the human ----------

def test_flag_for_review_asks_for_a_human():
    """conf in [0.60, 0.85) with prob outside the uncertain band."""
    ctx = DecisionContext(enabled=True, scorer=_FixedScorer(confidence=0.75, prob=0.20))
    a = decide(elements=[_make_element("Go")], goal="go", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.CLICK
    assert "flag_for_review" in a.reason
    assert a.needs_attention is True


def test_act_does_not_ask_for_a_human():
    """FakeJevScorer substring hit -> conf=0.95, prob=0.90 -> ACT."""
    ctx = DecisionContext(enabled=True)
    a = decide(elements=[_make_element("Submit"), _make_element("Cancel")],
               goal="submit", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.CLICK
    assert a.needs_attention is False


def test_fallback_does_not_ask_for_a_human():
    """conf below the low-stakes threshold -> FALLBACK, no human needed."""
    ctx = DecisionContext(enabled=True, scorer=_FixedScorer(confidence=0.40, prob=0.20))
    a = decide(elements=[_make_element("Go")], goal="go", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.BLOCKED
    assert a.needs_attention is False


def test_uncertain_does_not_ask_for_a_human():
    """prob inside the uncertain band -> UNCERTAIN, no human needed."""
    ctx = DecisionContext(enabled=True, scorer=_FixedScorer(confidence=0.75, prob=0.50))
    a = decide(elements=[_make_element("Go")], goal="go", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.BLOCKED
    assert a.needs_attention is False


def test_blocked_paths_do_not_ask_for_a_human():
    """Every blocked early return keeps the signal off."""
    no_elements = decide(elements=[], goal="go", ctx=DecisionContext(enabled=True))
    assert no_elements is not None
    assert no_elements.needs_attention is False

    ask_error = decide(elements=[_make_element("Go")], goal="go",
                       ctx=DecisionContext(enabled=True, scorer=_ErrorScorer()))
    assert ask_error is not None
    assert ask_error.needs_attention is False

    picked_none = decide(elements=[_make_element("Go")], goal="go",
                         ctx=DecisionContext(enabled=True,
                                             scorer=_FixedScorer(confidence=0.95, prob=0.95,
                                                                target_index=-1)))
    assert picked_none is not None
    assert picked_none.reason == "picked_none"
    assert picked_none.needs_attention is False

    invalid_target = decide(elements=[_make_element("Go")], goal="go",
                            ctx=DecisionContext(enabled=True,
                                                scorer=_FixedScorer(confidence=0.95, prob=0.95,
                                                                   target_index=7)))
    assert invalid_target is not None
    assert invalid_target.reason == "invalid_target"
    assert invalid_target.needs_attention is False