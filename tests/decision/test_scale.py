"""The local judge's confidence scale and the thresholds that belong to it."""
from __future__ import annotations

from openeyes.decision.action import ActionKind
from openeyes.decision.router import DecisionContext, decide
from openeyes.decision.scale import (
    HISTORY_VOTE_THRESHOLDS_EN,
    HISTORY_VOTE_THRESHOLDS_ZH,
    SCALE_PROBABILITY,
    SCALE_SUPPORT_SHARE,
    family_of,
    thresholds_for_scale,
)
from openeyes.decision.scorer import JevScore, JevScorer
from openeyes.decision.threshold import ThresholdConfig


def _element(name: str):
    from openeyes.core.schema import BBox, Center, Element

    return Element(
        backend="uia", control_type="Button", name=name,
        bbox=BBox(x=0, y=0, w=10, h=10), center=Center(x=5, y=5),
    )


class _ShareScorer(JevScorer):
    """A scorer whose answer is on the support-share scale."""

    def __init__(self, share: float, scale: str = SCALE_SUPPORT_SHARE) -> None:
        self.share = share
        self.scale = scale

    def ask(self, goal, candidates, *, app_name=""):
        return JevScore(target_index=0, confidence=self.share, prob=self.share,
                        latency_ms=0, scale=self.scale)


def test_thresholds_follow_the_scale():
    assert thresholds_for_scale(SCALE_PROBABILITY) == ThresholdConfig()
    assert thresholds_for_scale(SCALE_SUPPORT_SHARE) is HISTORY_VOTE_THRESHOLDS_ZH


def test_an_explicit_config_wins_over_the_scale():
    explicit = ThresholdConfig(low_stakes_threshold=0.20, high_stakes_threshold=0.42)
    assert thresholds_for_scale(SCALE_SUPPORT_SHARE, explicit) is explicit


def test_the_history_vote_profiles_are_ordered():
    for cfg in (HISTORY_VOTE_THRESHOLDS_ZH, HISTORY_VOTE_THRESHOLDS_EN):
        assert 0.0 <= cfg.low_stakes_threshold <= cfg.high_stakes_threshold <= 1.0
        assert cfg.uncertain_low <= cfg.uncertain_high


def test_the_family_comes_from_the_candidates():
    assert family_of(["消息", "文件"]) == "zh"
    assert family_of(["Save", "Search"]) == "en"
    assert thresholds_for_scale(SCALE_SUPPORT_SHARE, candidates=["消息"]) is HISTORY_VOTE_THRESHOLDS_ZH
    assert thresholds_for_scale(SCALE_SUPPORT_SHARE, candidates=["Save"]) is HISTORY_VOTE_THRESHOLDS_EN


def test_a_share_above_the_act_floor_acts():
    action = decide(elements=[_element("消息")], goal="点击消息按钮",
                    ctx=DecisionContext(enabled=True, scorer=_ShareScorer(0.95)))
    assert action is not None
    assert action.kind == ActionKind.CLICK
    assert "act" in action.reason


def test_a_middle_share_is_flagged_for_review():
    """0.34 is the decisive local share - but the measurement says it can be
    wrong, so it must not act on its own."""
    action = decide(elements=[_element("消息")], goal="点击消息按钮",
                    ctx=DecisionContext(enabled=True, scorer=_ShareScorer(0.341417)))
    assert action is not None
    assert action.kind == ActionKind.CLICK
    assert "flag_for_review" in action.reason


def test_a_small_share_falls_back():
    action = decide(elements=[_element("消息")], goal="点击消息按钮",
                    ctx=DecisionContext(enabled=True, scorer=_ShareScorer(0.10)))
    assert action is not None
    assert action.kind == ActionKind.BLOCKED
    assert "fallback" in action.reason


def test_the_english_family_has_no_review_band():
    """The English half of the history is not proven: below the top it is
    close to a coin flip, so it says nothing rather than guessing."""
    action = decide(elements=[_element("Save"), _element("Search")], goal="save the file",
                    ctx=DecisionContext(enabled=True, scorer=_ShareScorer(0.341417)))
    assert action is not None
    assert action.kind == ActionKind.BLOCKED
    assert "fallback" in action.reason


def test_the_same_number_means_something_else_on_the_hosted_scale():
    """0.34 is a fallback on the hosted scale and a review on the share
    scale - the thresholds belong to the scale, not to the number."""
    hosted = decide(elements=[_element("消息")], goal="点击消息按钮",
                    ctx=DecisionContext(enabled=True,
                                        scorer=_ShareScorer(0.341417, SCALE_PROBABILITY)))
    assert hosted is not None
    assert hosted.kind == ActionKind.BLOCKED
