"""The one verdict vocabulary and the one combination rule.

Every probe in this repository answers the same question - *did this surface
pass, fail, or could it not be judged here?* - so the answer must be written
down once. Three places used to answer it with different rules:

* ``examples/_acceptance_verdict.py`` combined with **FAIL > SKIP > PASS**
  (a skipped leg is never a pass);
* ``openeyes.agent.display_smoke.SmokeReport`` combined with
  **FAIL > PASS > SKIP** (one measured scenario hid two unmeasured ones);
* ``openeyes.agent.virtual_desktop.SecondDesktopReport`` did the same.

Two rules with opposite orders are not a style difference: the second one
reports ``PASS`` for a run that never exercised the scenario the smoke exists
for. Everything now goes through :func:`combine` here.

The rule, in three sentences:

1. ``FAIL`` dominates everything: one measured wrong is wrong;
2. ``SKIP`` beats ``PASS``: "it ran and was right" and "there was nothing to
   judge" are two different facts and may not be merged into a pass;
3. ``PASS`` only when there is at least one pass and nothing else.

Nothing else in the tree may define ``EXIT_CODES``; ``tests/test_verdict.py``
and ``tests/test_acceptance_verdict_shared.py`` fail if a second copy appears.
"""
from __future__ import annotations

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
#: A run that was stopped before it could conclude: not a verdict at all.
INTERRUPTED = "INTERRUPTED"

#: The three verdicts, in no particular order; membership checks only.
VERDICTS = (PASS, FAIL, SKIP)
#: The verdicts as a set, for filtering a mixed status sequence.
VERDICT_SET = frozenset(VERDICTS)

#: The only exit-code table in the repository.
EXIT_CODES = {PASS: 0, FAIL: 2, SKIP: 3}
#: An interrupted run does not get a verdict code.
EXIT_INTERRUPTED = 130


def combine(*verdicts: str) -> str:
    """Combine verdicts as **FAIL > SKIP > PASS**.

    Raises :class:`ValueError` on anything that is not a verdict, so a status
    such as ``"PLAN"`` or ``"DRY-RUN"`` cannot be smuggled into a total.
    """
    for verdict in verdicts:
        if verdict not in VERDICT_SET:
            raise ValueError(f"unknown verdict: {verdict!r}")
    if FAIL in verdicts:
        return FAIL
    if SKIP in verdicts:
        return SKIP
    if not verdicts:
        raise ValueError("no verdicts to combine")
    return PASS


def exit_code(verdict: str) -> int:
    """The process exit code that goes with a verdict."""
    try:
        return EXIT_CODES[verdict]
    except KeyError:
        raise ValueError(f"unknown verdict: {verdict!r}") from None


def combine_statuses(statuses) -> str:
    """Combine the parts that are verdicts, ignoring ``PLAN``/``DRY-RUN``.

    A report that never reached a verdict (dry run, nothing measured) is a
    ``SKIP``: it did not pass, it was not judged.
    """
    verdicts = [status for status in statuses if status in VERDICT_SET]
    if not verdicts:
        return SKIP
    return combine(*verdicts)
