"""Unit tests for openeyes.decision — contract parity with ACH FEAT-03 §1.1a."""
from __future__ import annotations
import pytest

from openeyes.core.schema import BBox, Center, Element
from openeyes.decision import (
    ThresholdConfig, classify_confidence, DecisionBand,
    Action, ActionKind,
    FakeJevScorer, JevScorer, JevScore, JevAskError,
    decide, DecisionContext,
)


# ---------- threshold ----------

def test_threshold_defaults_match_ach_feat03():
    """Aligned with ACH FEAT-03 §1.1a."""
    cfg = ThresholdConfig()
    assert cfg.confidence_floor == 0.50
    assert cfg.low_stakes_threshold == 0.60
    assert cfg.high_stakes_threshold == 0.85
    assert cfg.uncertain_low == 0.30
    assert cfg.uncertain_high == 0.70


def test_threshold_validation():
    with pytest.raises(ValueError):
        ThresholdConfig(high_stakes_threshold=1.5)
    with pytest.raises(ValueError):
        ThresholdConfig(low_stakes_threshold=0.9, high_stakes_threshold=0.5)
    with pytest.raises(ValueError):
        ThresholdConfig(uncertain_low=0.8, uncertain_high=0.3)


def test_classify_act_band():
    cfg = ThresholdConfig()
    assert classify_confidence(0.90, 0.95, cfg) == DecisionBand.ACT
    assert classify_confidence(0.85, 0.50, cfg) == DecisionBand.ACT  # high-stakes boundary


def test_classify_flag_for_review_band():
    cfg = ThresholdConfig()
    # prob=0.20 (outside uncertain band [0.30, 0.70]) + conf=0.70 -> FLAG_FOR_REVIEW
    assert classify_confidence(0.70, 0.20, cfg) == DecisionBand.FLAG_FOR_REVIEW
    assert classify_confidence(0.60, 0.10, cfg) == DecisionBand.FLAG_FOR_REVIEW  # low boundary


def test_classify_fallback_band():
    cfg = ThresholdConfig()
    # prob=0.20 (outside uncertain band) + conf=0.50 -> FALLBACK
    assert classify_confidence(0.50, 0.20, cfg) == DecisionBand.FALLBACK
    assert classify_confidence(0.0, 0.0, cfg) == DecisionBand.FALLBACK


def test_classify_uncertain_short_circuits():
    """Uncertain band beats flag_for_review/fallback when prob is in [0.30, 0.70]
    and confidence < high_stakes (0.85)."""
    cfg = ThresholdConfig()
    # prob=0.50, conf=0.70 -> would normally be flag_for_review, but
    # prob is in uncertain band AND conf < 0.85 -> UNCERTAIN
    assert classify_confidence(0.70, 0.50, cfg) == DecisionBand.UNCERTAIN
    # but ACT trumps UNCERTAIN (conf >= 0.85)
    assert classify_confidence(0.90, 0.50, cfg) == DecisionBand.ACT


def test_classify_rejects_out_of_range():
    cfg = ThresholdConfig()
    with pytest.raises(ValueError):
        classify_confidence(-0.1, 0.5, cfg)
    with pytest.raises(ValueError):
        classify_confidence(0.5, 1.5, cfg)


# ---------- action ----------

def test_action_to_dict():
    a = Action(kind=ActionKind.CLICK, target_index=3, reason="act")
    d = a.to_dict()
    assert d["kind"] == "click"
    assert d["target_index"] == 3
    assert d["reason"] == "act"


# ---------- scorer ----------

def test_fake_jev_empty_candidates_returns_none():
    s = FakeJevScorer()
    r = s.ask(goal="search", candidates=[])
    assert r.target_index == -1
    assert r.confidence == 0.50
    assert r.prob == 0.50


def test_fake_jev_empty_goal_returns_none():
    s = FakeJevScorer()
    r = s.ask(goal="", candidates=["foo", "bar"])
    assert r.target_index == -1


def test_fake_jev_substring_hit():
    s = FakeJevScorer()
    r = s.ask(goal="sub", candidates=["Submit", "Cancel"])
    assert r.target_index == 0
    assert r.confidence == 0.95
    assert r.prob == 0.90


def test_fake_jev_no_match_returns_first_with_low_confidence():
    """No substring or prefix match -> returns first candidate with confidence=0.55
    and `prob = 1/n + 0.01` (just above uniform so top-1 == choice under the
    contract). For 3 candidates, prob ≈ 0.343."""
    s = FakeJevScorer()
    r = s.ask(goal="zzz_nothing_matches", candidates=["Foo", "Bar", "Baz"])
    assert r.target_index == 0
    assert r.confidence == 0.55
    expected_prob = 1.0 / 3 + 0.01
    assert abs(r.prob - expected_prob) < 1e-9
    # Contract: probabilities dict must sum to ~1, top-1 = choice
    assert r.probabilities is not None
    items = dict(r.probabilities)
    assert abs(sum(items.values()) - 1.0) < 0.02
    assert items["0"] >= max(items.values()) - 1e-6


def test_fake_jev_default_first_candidate():
    s = FakeJevScorer()
    r = s.ask(goal="zzz", candidates=["Foo", "Bar"])
    assert r.target_index == 0
    assert r.confidence == 0.55


def test_fake_jev_always_pick_none_rule():
    s = FakeJevScorer(rules={"always_pick_none": True})
    r = s.ask(goal="submit", candidates=["Submit", "Cancel"])
    assert r.target_index == -1


def test_jevscore_validates_range():
    with pytest.raises(ValueError):
        JevScore(target_index=0, confidence=1.5, prob=0.5, latency_ms=0)
    with pytest.raises(ValueError):
        JevScore(target_index=0, confidence=0.5, prob=-0.1, latency_ms=0)


# ---------- router ----------

def _make_element(name: str, control_type: str = "Button") -> Element:
    return Element(
        backend="uia", control_type=control_type, name=name,
        bbox=BBox(x=0, y=0, w=10, h=10), center=Center(x=5, y=5),
    )


def test_decide_disabled_returns_none():
    ctx = DecisionContext(enabled=False)
    assert decide(elements=[_make_element("OK")], goal="ok", ctx=ctx) is None


def test_decide_enabled_empty_elements_returns_blocked():
    ctx = DecisionContext(enabled=True)
    a = decide(elements=[], goal="anything", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.BLOCKED
    assert a.reason == "no_elements"


def test_decide_enabled_act_path():
    """FakeJevScorer substring hit -> conf=0.95, prob=0.90 -> ACT."""
    ctx = DecisionContext(enabled=True)
    elements = [_make_element("Submit"), _make_element("Cancel")]
    a = decide(elements=elements, goal="submit", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.CLICK
    assert a.target_index == 0


def test_decide_enabled_flag_for_review_path():
    """FakeJevScorer prefix hit -> conf=0.80, prob=0.70 -> UNCERTAIN
    (prob in uncertain band + conf < 0.85). Use a prob outside the
    uncertain band to land in FLAG_FOR_REVIEW.
    """
    s = FakeJevScorer(rules={"force_prob": 0.85})
    # Without a custom-rule path, FakeJevScorer returns prob=0.70 on prefix hits
    # which lands in UNCERTAIN. To exercise FLAG_FOR_REVIEW we need a different
    # scorer that returns conf in [0.60, 0.85) and prob outside [0.30, 0.70].
    # Simulate via a small custom scorer.
    class CustomScorer(JevScorer):
        def ask(self, goal, candidates, *, app_name=""):
            return JevScore(target_index=0, confidence=0.75, prob=0.20, latency_ms=0)
    ctx = DecisionContext(enabled=True, scorer=CustomScorer())
    a = decide(elements=[_make_element("Go")], goal="go", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.CLICK
    assert "flag_for_review" in a.reason


def test_decide_enabled_fallback_path():
    class CustomScorer(JevScorer):
        def ask(self, goal, candidates, *, app_name=""):
            return JevScore(target_index=0, confidence=0.40, prob=0.20, latency_ms=0)
    ctx = DecisionContext(enabled=True, scorer=CustomScorer())
    a = decide(elements=[_make_element("Go")], goal="go", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.BLOCKED
    assert "fallback" in a.reason


def test_decide_enabled_uncertain_path():
    """Custom scorer that returns prob in uncertain band [0.30, 0.70]
    with confidence < highStakes (0.85) -> UNCERTAIN short-circuit."""
    class CustomScorer(JevScorer):
        def ask(self, goal, candidates, *, app_name=""):
            return JevScore(target_index=0, confidence=0.75, prob=0.50, latency_ms=0)
    ctx = DecisionContext(enabled=True, scorer=CustomScorer())
    elements = [_make_element("Foo"), _make_element("Bar")]
    a = decide(elements=elements, goal="anything", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.BLOCKED
    assert "uncertain" in a.reason


def test_decide_picked_none_returns_blocked():
    class CustomScorer(JevScorer):
        def ask(self, goal, candidates, *, app_name=""):
            return JevScore(target_index=-1, confidence=0.95, prob=0.95, latency_ms=0)
    ctx = DecisionContext(enabled=True, scorer=CustomScorer())
    a = decide(elements=[_make_element("Go")], goal="go", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.BLOCKED
    assert a.reason == "picked_none"


def test_decide_jev_ask_error_falls_back_to_blocked():
    class ErrorScorer(JevScorer):
        def ask(self, goal, candidates, *, app_name=""):
            raise JevAskError("simulated 503")
    ctx = DecisionContext(enabled=True, scorer=ErrorScorer())
    a = decide(elements=[_make_element("Go")], goal="go", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.BLOCKED
    assert "jev_ask_error" in a.reason


def test_decide_enabled_uses_fake_jev_by_default():
    """If enabled=True and scorer not passed, default to FakeJevScorer
    (no network). This is the safety guarantee — enabled must never
    silently call the real API."""
    ctx = DecisionContext(enabled=True)
    elements = [_make_element("Submit")]
    a = decide(elements=elements, goal="submit", ctx=ctx)
    assert a is not None
    assert a.kind == ActionKind.CLICK


# ---------- validate_probabilities (jev-ultrafast contract parity) ----------

def test_validate_probabilities_happy_path():
    """All five rules pass."""
    from openeyes.decision.scorer import validate_probabilities
    validate_probabilities({"0": 0.7, "1": 0.3}, "0", ["0", "1"])


def test_validate_probabilities_rejects_choice_not_in_ids():
    from openeyes.decision.scorer import validate_probabilities
    with pytest.raises(ValueError, match="not in candidate_ids"):
        validate_probabilities({"0": 0.7, "1": 0.3}, "2", ["0", "1"])


def test_validate_probabilities_rejects_missing_keys():
    from openeyes.decision.scorer import validate_probabilities
    with pytest.raises(ValueError, match="!="):
        validate_probabilities({"0": 1.0}, "0", ["0", "1"])


def test_validate_probabilities_rejects_extra_keys():
    from openeyes.decision.scorer import validate_probabilities
    with pytest.raises(ValueError, match="!="):
        validate_probabilities({"0": 0.5, "1": 0.3, "2": 0.2}, "0", ["0", "1"])


def test_validate_probabilities_rejects_out_of_range_value():
    from openeyes.decision.scorer import validate_probabilities
    with pytest.raises(ValueError, match="not finite"):
        validate_probabilities({"0": 1.5, "1": -0.5}, "0", ["0", "1"])


def test_validate_probabilities_rejects_nan():
    from openeyes.decision.scorer import validate_probabilities
    with pytest.raises(ValueError, match="not finite"):
        validate_probabilities({"0": float("nan"), "1": 1.0}, "0", ["0", "1"])


def test_validate_probabilities_rejects_sum_not_one():
    from openeyes.decision.scorer import validate_probabilities
    with pytest.raises(ValueError, match="sum"):
        validate_probabilities({"0": 0.5, "1": 0.2}, "0", ["0", "1"])  # sum=0.7


def test_validate_probabilities_rejects_choice_not_top1():
    from openeyes.decision.scorer import validate_probabilities
    with pytest.raises(ValueError, match="not top-1"):
        validate_probabilities({"0": 0.3, "1": 0.7}, "0", ["0", "1"])  # top-1=1, choice=0


def test_validate_probabilities_tolerance_one_percent():
    """Tolerance is 0.02; sum=0.99 should pass."""
    from openeyes.decision.scorer import validate_probabilities
    validate_probabilities({"0": 0.50, "1": 0.49}, "0", ["0", "1"])


# ---------- inject_probabilities rule (contract-violation testing) ----------

def test_fake_jev_inject_probabilities_valid():
    """Inject a valid full probabilities dict -> scorer returns it verbatim."""
    s = FakeJevScorer(rules={
        "inject_probabilities": {"0": 0.25, "1": 0.55, "2": 0.20},
        "inject_choice": "1",
    })
    r = s.ask(goal="anything", candidates=["a", "b", "c"])
    assert r.target_index == 1
    assert r.prob == 0.55
    assert r.confidence == 0.95
    assert r.probabilities is not None


def test_fake_jev_inject_probabilities_invalid_choice_raises():
    """Injecting an invalid (sum≠1) dict raises — same contract a real JevScorer would face."""
    s = FakeJevScorer(rules={
        "inject_probabilities": {"0": 0.6, "1": 0.6},
        "inject_choice": "0",
    })
    with pytest.raises(ValueError, match="sum"):
        s.ask(goal="anything", candidates=["a", "b"])


# ---------- probabilities field on JevScore ----------

def test_jevscore_probabilities_field_is_frozen():
    """probabilities is normalized to frozenset for hashability."""
    s = FakeJevScorer()
    r = s.ask(goal="submit", candidates=["Submit", "Cancel"])
    assert isinstance(r.probabilities, frozenset)


def test_fake_jev_single_candidate_fallback_probability_is_valid():
    score = FakeJevScorer().ask(goal="click the Save button", candidates=["Save"])
    assert score.target_index == 0
    assert score.prob == 1.0
    assert score.probabilities == frozenset({("0", 1.0)})


def test_jevscore_rejects_non_dict_probabilities():
    """probabilities must be dict or frozenset."""
    with pytest.raises(TypeError):
        JevScore(target_index=0, confidence=0.5, prob=0.5, latency_ms=0,
                 probabilities="not a dict")


# ---------- System One criteria (label keys) ----------

def test_criteria_uses_candidate_names_as_keys():
    """The System One protocol names options by the criteria KEY."""
    from openeyes.decision.scorer import _criteria_for
    criteria, mapping = _criteria_for(["消息", "文件"])
    assert list(criteria) == ["消息", "文件"]
    assert criteria == {"消息": "消息", "文件": "文件"}
    assert mapping == {"消息": 0, "文件": 1}


def test_criteria_falls_back_to_index_keys():
    """Empty or duplicated candidate names fall back to index keys."""
    from openeyes.decision.scorer import _criteria_for
    criteria, mapping = _criteria_for(["", "文件"])
    assert list(criteria) == ["0", "1"]
    criteria, mapping = _criteria_for(["同", "同"])
    assert list(criteria) == ["0", "1"]
    assert mapping == {"0": 0, "1": 1}


def test_typesafe_scorer_maps_name_keyed_answer_back_to_index(monkeypatch):
    """A name-keyed answer maps back to the candidate index."""
    import json
    from openeyes.decision import scorer as scorer_mod

    payload = {
        "answers": {
            "target": {
                "type": "choice",
                "choice": "文件",
                "confidence": 0.8,
                "probabilities": {"消息": 0.2, "文件": 0.8},
            }
        }
    }
    captured = {}

    class _Response:
        def read(self):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr(scorer_mod.urllib.request, "urlopen", fake_urlopen)
    scorer = scorer_mod.TypesafeJevScorer("local-key", base_url="http://127.0.0.1:9201/v1")
    score = scorer.ask(goal="open the file menu", candidates=["消息", "文件"], app_name="openeyes")

    assert captured["url"] == "http://127.0.0.1:9201/v1/systemone"
    assert captured["body"]["questions"]["target"]["criteria"] == {"消息": "消息", "文件": "文件"}
    assert score.target_index == 1
    assert score.prob == 0.8
    assert score.probabilities == frozenset({("0", 0.2), ("1", 0.8)})
# ---------- local XERJ node (keyless loopback endpoint) ----------

def _response(payload):
    import json

    class _Response:
        def read(self):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Response()


def _capture(monkeypatch, scorer_mod, payload):
    import json

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _response(payload)

    monkeypatch.setattr(scorer_mod.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_typesafe_scorer_omits_authorization_header_without_key(monkeypatch):
    """A keyless local node gets no Authorization header at all."""
    from openeyes.decision import scorer as scorer_mod

    captured = _capture(monkeypatch, scorer_mod, {
        "answers": {
            "target": {
                "type": "choice",
                "choice": "\u6587\u4ef6",
                "confidence": 0.6,
                "probabilities": {"\u6d88\u606f": 0.4, "\u6587\u4ef6": 0.6},
            }
        }
    })
    scorer = scorer_mod.TypesafeJevScorer("")
    score = scorer.ask(goal="open the file menu", candidates=["\u6d88\u606f", "\u6587\u4ef6"],
                       app_name="openeyes")

    assert "Authorization" not in captured["headers"]
    assert score.target_index == 1
    assert score.prob == 0.6


def test_typesafe_scorer_sends_bearer_scheme_with_key(monkeypatch):
    """The hosted bearer scheme is also what a local XERJ node accepts."""
    from openeyes.decision import scorer as scorer_mod

    captured = _capture(monkeypatch, scorer_mod, {
        "answers": {
            "target": {
                "type": "choice",
                "choice": "\u6587\u4ef6",
                "confidence": 0.6,
                "probabilities": {"\u6d88\u606f": 0.4, "\u6587\u4ef6": 0.6},
            }
        }
    })
    scorer = scorer_mod.TypesafeJevScorer("local-key", base_url="http://127.0.0.1:9201/v1")
    scorer.ask(goal="open the file menu", candidates=["\u6d88\u606f", "\u6587\u4ef6"],
               app_name="openeyes")

    assert captured["headers"]["Authorization"] == "Bearer local-key"
    assert captured["url"] == "http://127.0.0.1:9201/v1/systemone"


def test_typesafe_scorer_accepts_support_scores_without_distribution(monkeypatch):
    """A local history vote returns support scores, not a distribution."""
    from openeyes.decision import scorer as scorer_mod

    _capture(monkeypatch, scorer_mod, {
        "model": "xerj-history-vote-1",
        "answers": {
            "target": {
                "type": "choice",
                "choice": "\u6d88\u606f",
                "confidence": 0.512126,
                "probabilities": {"\u6d88\u606f": 0.512126, "\u6587\u4ef6": 0.0},
            }
        },
        "decisions": {"index": "judgements", "k": 10},
    })
    scorer = scorer_mod.TypesafeJevScorer("local-key", base_url="http://127.0.0.1:9201/v1")
    score = scorer.ask(goal="click the message button", candidates=["\u6d88\u606f", "\u6587\u4ef6"],
                       app_name="openeyes")

    assert score.target_index == 0
    assert score.prob == 0.512126
    assert score.probabilities == frozenset({("0", 0.512126), ("1", 0.0)})


def test_typesafe_scorer_fills_missing_support_labels_with_zero(monkeypatch):
    """A candidate the vote never scored is support 0, not a contract break."""
    from openeyes.decision import scorer as scorer_mod

    _capture(monkeypatch, scorer_mod, {
        "model": "xerj-history-vote-1",
        "answers": {
            "target": {
                "type": "choice",
                "choice": "\u6d88\u606f",
                "confidence": 0.5,
                "probabilities": {"\u6d88\u606f": 0.5},
            }
        },
        "decisions": {"index": "judgements", "k": 10},
    })
    scorer = scorer_mod.TypesafeJevScorer("local-key", base_url="http://127.0.0.1:9201/v1")
    score = scorer.ask(goal="click the message button", candidates=["\u6d88\u606f", "\u6587\u4ef6"],
                       app_name="openeyes")

    assert score.probabilities == frozenset({("0", 0.5), ("1", 0.0)})


def test_typesafe_scorer_surfaces_http_error_status_and_reason(monkeypatch):
    """A 422 from the node says what went wrong, not just 'request failed'."""
    import io
    import json
    import urllib.error

    from openeyes.decision import scorer as scorer_mod

    payload = json.dumps({
        "error": {"type": "no_support", "reason": "no labelled neighbour in `judgements`"}
    }).encode("utf-8")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 422, "Unprocessable Entity", {}, io.BytesIO(payload)
        )

    monkeypatch.setattr(scorer_mod.urllib.request, "urlopen", fake_urlopen)
    scorer = scorer_mod.TypesafeJevScorer("local-key", base_url="http://127.0.0.1:9201/v1")

    with pytest.raises(scorer_mod.JevAskError) as excinfo:
        scorer.ask(goal="buy a ticket to mars", candidates=["\u8d2d\u4e70", "\u9000\u8d27"],
                   app_name="openeyes")

    assert "422" in str(excinfo.value)
    assert "no labelled neighbour" in str(excinfo.value)
