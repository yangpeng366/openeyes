"""The local judge: how the decision layer names candidates, and how it
fails when the judge cannot answer.

The local XERJ node retrieves neighbours by the question text, so it only
answers the plain option names its history has labels for, and it answers
nothing at all for a name it has never seen. Both facts belong to the
decision layer's contract, so they are pinned here.
"""
from __future__ import annotations

from openeyes.decision.history import load_probe, load_seed
from openeyes.decision.scorer import JevAskError, JevScore, JevScorer
from openeyes.record.generator import generate_trajectory_from_prompt


def _inventory():
    return [
        {"name": "消息", "control_type": "Button", "automation_id": "btn_msg", "interactive": True},
        {"name": "文件", "control_type": "Button", "automation_id": "btn_file", "interactive": True},
    ]


class _RecordingScorer(JevScorer):
    """Records the candidates it was handed."""

    candidate_key_style = "descriptive"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def ask(self, goal, candidates, *, app_name=""):
        self.seen = list(candidates)
        return JevScore(target_index=0, confidence=0.95, prob=0.90, latency_ms=0)


class _PlainScorer(_RecordingScorer):
    """A local judge: it wants the plain option names."""

    candidate_key_style = "name"


class _FailingScorer(JevScorer):
    def ask(self, goal, candidates, *, app_name=""):
        raise JevAskError("Jev request failed with HTTP 422: no labelled neighbour")


def test_the_hosted_judge_gets_descriptive_candidates():
    scorer = _RecordingScorer()
    generate_trajectory_from_prompt(
        "click the message button",
        app_name="Edge",
        element_inventory=_inventory(),
        backend="jev",
        jev_scorer=scorer,
    )
    assert scorer.seen == [
        "Button: 消息 (automation_id=btn_msg)",
        "Button: 文件 (automation_id=btn_file)",
    ]


def test_the_local_judge_gets_plain_candidates():
    scorer = _PlainScorer()
    generate_trajectory_from_prompt(
        "click the message button",
        app_name="Edge",
        element_inventory=_inventory(),
        backend="jev",
        jev_scorer=scorer,
    )
    assert scorer.seen == ["消息", "文件"]


def test_a_judge_that_cannot_answer_yields_no_step():
    """A 422 must not become a click: no step is better than a wrong step."""
    trajectory = generate_trajectory_from_prompt(
        "click the message button",
        app_name="Edge",
        element_inventory=_inventory(),
        backend="jev",
        jev_scorer=_FailingScorer(),
    )
    assert trajectory.steps == []


def test_the_history_and_the_probe_cover_the_same_options():
    seed = load_seed()
    probe = load_probe()
    assert len(seed) == 84          # 21 actions x 2 phrasings x 2 languages
    assert len(probe) == 42         # 21 actions x 1 held-out phrasing x 2 languages
    assert {example.label for example in seed} == {example.label for example in probe}
    assert len({example.id for example in seed}) == len(seed)
    assert len({example.id for example in probe}) == len(probe)


def test_every_distractor_is_an_option_of_the_same_family():
    """The node holds one index, so a family must not point at the other's
    options - a Chinese goal retrieves the Chinese documents only."""
    labels = {}
    for example in load_seed():
        labels.setdefault(example.lang, set()).add(example.label)
    for example in load_seed() + load_probe():
        assert set(example.distractors) <= labels[example.lang]


def test_goal_and_option_share_a_language():
    """A Chinese goal with a Chinese option, an English goal with an English
    option: the first history mixed the two, and matched neither."""
    for example in load_seed() + load_probe():
        han = any("\u4e00" <= character <= "\u9fff" for character in example.label)
        assert han == (example.lang == "zh")
        assert han == any("\u4e00" <= character <= "\u9fff" for character in example.goal)


def test_the_document_text_carries_goal_and_options():
    example = load_seed()[0]
    assert example.text == " | ".join((example.goal, example.label, *example.distractors))
