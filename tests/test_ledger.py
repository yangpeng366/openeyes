"""Tests for the action ledger.

The ledger is the trace a run leaves behind, so it has to answer two
questions honestly:

* what did the automation do, and by which path;
* how much of that reached the user's own hands.

A ledger that flatters the automation is worse than no ledger, so the
tests below pin the counting rules rather than just the API.
"""
from __future__ import annotations

from openeyes.agent.ledger import (
    DELIVERY_NONE,
    DELIVERY_PIXEL,
    DELIVERY_SEMANTIC,
    ActionLedger,
    delivery_for_method,
    record_action,
)


# --------------------------------------------------------------- the empty ledger
def test_a_fresh_ledger_is_empty():
    ledger = ActionLedger()
    assert ledger.entries() == []
    summary = ledger.summary()
    assert summary["actions"] == 0
    assert summary["physical_inputs"] == 0
    assert summary["physical_input_rate"] == 0.0
    assert summary["deliveries"] == {}


# ------------------------------------------------------------------ what is kept
def test_an_entry_keeps_what_it_was_given():
    ledger = ActionLedger(agent_id="worker-a")
    entry = ledger.record(action="invoke", delivery=DELIVERY_SEMANTIC,
                          target="Save", ok=True, verified=True)
    assert entry.agent_id == "worker-a"
    assert entry.action == "invoke"
    assert entry.delivery == DELIVERY_SEMANTIC
    assert entry.target == "Save"
    assert entry.verified is True
    assert entry.ts > 0


def test_a_truthy_flag_is_stored_as_a_flag():
    ledger = ActionLedger()
    entry = ledger.record(action="pixel", physical_input=1, focus_stolen="yes")
    assert entry.physical_input is True
    assert entry.focus_stolen is True


def test_an_entry_may_name_its_own_agent():
    """Several agents may share one ledger and still be told apart."""
    ledger = ActionLedger(agent_id="default")
    entry = ledger.record(action="invoke", agent_id="worker-b")
    assert entry.agent_id == "worker-b"
    assert ledger.summary()["agent_id"] == "default"


# ------------------------------------------------------------------- the counts
def test_the_summary_counts_the_physical_inputs():
    ledger = ActionLedger()
    ledger.record(action="invoke", delivery=DELIVERY_SEMANTIC)
    ledger.record(action="pixel", delivery=DELIVERY_PIXEL,
                  physical_input=True, cursor_moved=True)
    ledger.record(action="pixel", delivery=DELIVERY_PIXEL,
                  physical_input=True, cursor_moved=True)
    summary = ledger.summary()
    assert summary["actions"] == 3
    assert summary["physical_inputs"] == 2
    assert summary["physical_input_rate"] == 0.6667
    assert summary["cursor_moves"] == 2
    assert summary["focus_stolen"] == 0


def test_the_summary_counts_by_delivery_path():
    ledger = ActionLedger()
    ledger.record(action="invoke", delivery=DELIVERY_SEMANTIC)
    ledger.record(action="pixel", delivery=DELIVERY_PIXEL)
    ledger.record(action="pixel", delivery=DELIVERY_PIXEL)
    summary = ledger.summary()
    assert summary["deliveries"][DELIVERY_SEMANTIC] == 1
    assert summary["deliveries"][DELIVERY_PIXEL] == 2
    assert summary["actions_by_kind"]["pixel"] == 2


def test_the_summary_counts_what_worked():
    ledger = ActionLedger()
    ledger.record(action="invoke", ok=True, verified=True)
    ledger.record(action="invoke", ok=False, verified=False)
    summary = ledger.summary()
    assert summary["ok"] == 1
    assert summary["verified"] == 1


# -------------------------------------------------------------------- the copies
def test_entries_are_a_copy_of_what_was_recorded():
    """A caller must not be able to rewrite the past through the list."""
    ledger = ActionLedger()
    ledger.record(action="invoke")
    entries = ledger.entries()
    entries.append("junk")
    assert len(ledger.entries()) == 1


# ------------------------------------------------------------- delivery naming
def test_a_known_method_names_its_delivery_path():
    assert delivery_for_method("invoke") == DELIVERY_SEMANTIC
    assert delivery_for_method("pixel") == DELIVERY_PIXEL
    assert delivery_for_method("INVOKE") == DELIVERY_SEMANTIC


def test_an_unknown_method_is_delivered_by_no_known_path():
    """Say "we do not know" rather than guess a cheaper path than the truth."""
    assert delivery_for_method(None) == DELIVERY_NONE
    assert delivery_for_method("") == DELIVERY_NONE
    assert delivery_for_method("teleport") == DELIVERY_NONE

# ---------------------------------------------------------- the shared helper
def test_record_action_derives_the_delivery_path():
    """One place decides what a backend's method means."""
    ledger = ActionLedger()
    entry = record_action(ledger, action="pixel", method="pixel",
                          physical_input=True, verified=True)
    assert entry.delivery == DELIVERY_PIXEL
    assert entry.physical_input is True
    assert entry.verified is True
    assert ledger.summary()["actions"] == 1


def test_record_action_says_none_when_the_method_is_unknown():
    ledger = ActionLedger()
    entry = record_action(ledger, action="none", method=None)
    assert entry.delivery == DELIVERY_NONE
    assert ledger.summary()["deliveries"][DELIVERY_NONE] == 1
