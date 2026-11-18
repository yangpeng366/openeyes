"""One verdict vocabulary, one combination rule, for the whole tree.

``openeyes/core/verdict.py`` is the single definition. These tests pin the rule
itself - **FAIL > SKIP > PASS** - and the two ways a caller can misuse it, so a
future "small fix" that flips the order turns the suite red instead of turning
an unexercised scenario into a pass.
"""
from __future__ import annotations

import pytest

from openeyes.core.verdict import (
    EXIT_CODES,
    EXIT_INTERRUPTED,
    FAIL,
    PASS,
    SKIP,
    VERDICTS,
    combine,
    combine_statuses,
    exit_code,
)


def test_skip_beats_pass_because_an_unjudged_surface_is_not_a_pass():
    assert combine(PASS, SKIP) == SKIP
    assert combine(SKIP, PASS) == SKIP
    assert combine(PASS, PASS, SKIP) == SKIP


def test_fail_beats_both():
    assert combine(PASS, SKIP, FAIL) == FAIL
    assert combine(SKIP, FAIL) == FAIL
    assert combine(FAIL) == FAIL


def test_pass_needs_at_least_one_pass_and_nothing_else():
    assert combine(PASS) == PASS
    assert combine(PASS, PASS) == PASS


def test_every_verdict_lives_in_the_vocabulary():
    assert set(VERDICTS) == {PASS, FAIL, SKIP}


@pytest.mark.parametrize("junk", ["passed", "PLAN", "DRY-RUN", "", "ok"])
def test_a_non_verdict_cannot_be_combined(junk):
    with pytest.raises(ValueError):
        combine(PASS, junk)


def test_nothing_to_combine_is_an_error_not_a_pass():
    with pytest.raises(ValueError):
        combine()


def test_combine_statuses_ignores_the_states_that_are_not_verdicts():
    assert combine_statuses([PASS, "PLAN"]) == PASS
    assert combine_statuses([PASS, SKIP, "PLAN"]) == SKIP
    assert combine_statuses(["PLAN", "DRY-RUN"]) == SKIP
    assert combine_statuses([]) == SKIP


def test_the_exit_table_is_the_documented_one():
    assert EXIT_CODES == {PASS: 0, FAIL: 2, SKIP: 3}
    assert EXIT_INTERRUPTED == 130
    assert [exit_code(PASS), exit_code(FAIL), exit_code(SKIP)] == [0, 2, 3]
    with pytest.raises(ValueError):
        exit_code("DRY-RUN")
