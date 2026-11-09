"""Tests for the agent's visible presence.

The chain under test is the one the whole direction rests on: the
decision layer says a human should look, the attention budget says
there is attention to spare, and only then does a cursor move.
"""
from __future__ import annotations

from openeyes.agent.attention import AttentionBudget
from openeyes.agent.overlay import CursorOverlay
from openeyes.agent.presence import AgentPresence


class _FakeOverlay:
    """A stand-in for the overlay window: it records what was asked."""

    def __init__(self) -> None:
        self.agent_id = "worker-a"
        self.label = ""
        self.color = "#00c853"
        self.state = type("State", (), {"visible": False})()
        self.shown_at: list[tuple[int, int]] = []
        self.closed = False

    def show(self, x: int, y: int) -> bool:
        self.shown_at.append((x, y))
        self.state.visible = True
        return True

    def hide(self) -> None:
        self.state.visible = False

    def close(self) -> None:
        self.closed = True


def _presence(per_minute: int = 1):
    overlay = _FakeOverlay()
    attention = AttentionBudget(per_minute=per_minute)
    return AgentPresence(overlay, attention), overlay, attention


# ----------------------------------------------------------------- the chain
def test_nothing_to_show_means_no_cursor():
    """An agent with no reason to be seen must not move in front of you."""
    presence, overlay, attention = _presence(per_minute=5)
    assert presence.announce(needs_attention=False, x=10, y=20) is False
    assert overlay.shown_at == []
    assert attention.used == 0


def test_a_reason_to_be_seen_spends_the_budget():
    presence, overlay, attention = _presence(per_minute=5)
    assert presence.announce(needs_attention=True, x=10, y=20) is True
    assert overlay.shown_at == [(10, 20)]
    assert attention.used == 1


def test_the_budget_stops_the_second_one():
    """One interruption a minute: the second agent waits its turn."""
    presence, overlay, attention = _presence(per_minute=1)
    assert presence.announce(needs_attention=True, x=10, y=20) is True
    assert presence.announce(needs_attention=True, x=30, y=40) is False
    assert overlay.state.visible is False
    assert attention.refused == 1
    assert presence.refused == 1


def test_a_refused_announcement_hides_a_visible_cursor():
    """An agent that lost the budget must not leave its cursor behind."""
    presence, overlay, attention = _presence(per_minute=1)
    presence.announce(needs_attention=True, x=10, y=20)
    attention.spend()          # the minute is used up
    assert presence.announce(needs_attention=True, x=30, y=40) is False
    assert overlay.state.visible is False


def test_withdrawing_takes_the_cursor_away():
    presence, overlay, _attention = _presence(per_minute=5)
    presence.announce(needs_attention=True, x=10, y=20)
    presence.withdraw()
    assert presence.shown is False
    assert overlay.state.visible is False


def test_closing_ends_the_presence():
    presence, overlay, _attention = _presence(per_minute=5)
    presence.announce(needs_attention=True, x=10, y=20)
    presence.close()
    assert overlay.closed is True
    assert presence.shown is False


def test_the_presence_reports_its_state():
    presence, _overlay, _attention = _presence(per_minute=2)
    presence.announce(needs_attention=True, x=1, y=2)
    state = presence.to_dict()
    assert state["agent_id"] == "worker-a"
    assert state["shown"] is True
    assert state["attention"]["per_minute"] == 2


def test_a_real_overlay_is_accepted():
    """The glue works with the real overlay, not only with the stand-in."""
    overlay = CursorOverlay(agent_id="worker-a", label="A", color="#00c853")
    presence = AgentPresence(overlay, AttentionBudget(per_minute=1))
    assert presence.announce(needs_attention=True, x=5, y=5) is True
    assert presence.shown is True
    presence.close()


# ------------------------------------------------------------- the public door
def test_the_agent_layer_exports_its_public_parts():
    """The package root is the public door; these parts hang there too."""
    import openeyes.agent as agent

    for name in (
        "AttentionBudget",
        "Scheduler",
        "Conflict",
        "IsolationConfig",
        "IsolationVerdict",
        "evaluate_isolation",
        "AgentPresence",
        "LEVEL_NONE",
        "LEVEL_SESSION",
        "LEVEL_VM",
        "ALL_LEVELS",
    ):
        assert name in agent.__all__
        assert hasattr(agent, name)


def test_the_exports_stay_in_one_order():
    """The list is the package's table of contents; it reads in one order."""
    import openeyes.agent as agent

    assert agent.__all__ == sorted(agent.__all__)


def test_an_agent_with_nothing_to_show_is_not_refused():
    """Quiet is not interference: nobody turned the agent down."""
    presence, overlay, attention = _presence(per_minute=5)
    for _ in range(3):
        assert presence.announce(needs_attention=False, x=1, y=2) is False
    assert presence.refused == 0
    assert attention.refused == 0
    assert overlay.shown_at == []