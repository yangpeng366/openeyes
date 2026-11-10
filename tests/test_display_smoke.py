"""The multi-monitor smoke decides honestly, from an injected display list.

CI has no second monitor, no negative-coordinate screen and usually no
sealed DPI, so the interesting logic is the *decision*: which scenario can
run here, and what does the report say when one cannot. These tests feed
fabricated :class:`~openeyes.agent.virtual_desktop.Display` lists to the
same planner the real script uses, then pin the click comparison and the
overall verdict.

No window is created and no pointer moves: the ``--go`` path is measured on
a real machine by ``examples/multi-monitor-smoke.py --go``.
"""
from __future__ import annotations

import pytest

from openeyes.agent.display_smoke import (
    DRY_RUN,
    FAIL,
    NEGATIVE,
    OFF_ORIGIN,
    PASS,
    PLAN,
    SCALED,
    SKIP,
    WINDOW_SIZE,
    SmokeReport,
    Step,
    plan_scenarios,
    run,
    verify_click,
    window_position,
)
from openeyes.agent.virtual_desktop import Display


def _display(device: str, rect: tuple[int, int, int, int], *,
             dpi: int = 96, scale: int = 100,
             primary: bool = False) -> Display:
    return Display(device=device, rect=rect, work=rect, dpi=dpi, scale=scale,
                   primary=primary)


def _by_name(steps: list[Step]) -> dict[str, Step]:
    return {step.name: step for step in steps}


# ------------------------------------------------------- a single monitor
def test_one_monitor_at_the_origin_plans_the_offset_window_only():
    steps = _by_name(plan_scenarios([_display(r"\\.\DISPLAY1", (0, 0, 2240, 1400),
                                              dpi=125, scale=130, primary=True)]))

    assert steps[OFF_ORIGIN].status == PLAN
    assert steps[OFF_ORIGIN].window_position != (0, 0)
    assert steps[SCALED].status == PLAN
    assert steps[SCALED].scale == 130
    assert steps[NEGATIVE].status == SKIP
    # the skip carries the reason, not a shrug
    assert "negative origin" in steps[NEGATIVE].detail
    assert r"\.\DISPLAY1 at (0,0)" in steps[NEGATIVE].detail


def test_a_100_percent_machine_skips_the_scaling_scenario():
    steps = _by_name(plan_scenarios([_display(r"\\.\DISPLAY1", (0, 0, 1920, 1080),
                                              dpi=96, scale=100, primary=True)]))

    assert steps[SCALED].status == SKIP
    assert "125% or 130%" in steps[SCALED].detail
    assert "100%" in steps[SCALED].detail


# --------------------------------------------------- a negative secondary
def test_a_monitor_left_of_the_primary_gets_a_negative_click():
    displays = [
        _display(r"\\.\DISPLAY2", (0, 0, 1920, 1080), primary=True),
        _display(r"\\.\DISPLAY1", (-1920, 0, 0, 1080), dpi=120, scale=125),
    ]
    steps = _by_name(plan_scenarios(displays))

    negative = steps[NEGATIVE]
    assert negative.status == PLAN
    assert negative.display == r"\\.\DISPLAY1"
    left, top = negative.window_position
    assert left < 0
    # the whole window, and the click, stay on the left-hand monitor
    width, height = negative.window_size
    assert left + width <= 0
    assert (left, left + width) != (0, 0)


def test_a_monitor_above_the_primary_is_a_negative_origin_too():
    displays = [
        _display(r"\\.\DISPLAY1", (0, 0, 1920, 1080), primary=True),
        _display(r"\\.\DISPLAY3", (0, -1080, 1920, 0)),
    ]
    assert _by_name(plan_scenarios(displays))[NEGATIVE].status == PLAN


def test_a_display_too_small_for_the_window_is_skipped_with_sizes():
    steps = _by_name(plan_scenarios([_display(r"\\.\DISPLAY1",
                                              (0, 0, 300, 200), primary=True)]))

    assert steps[OFF_ORIGIN].status == SKIP
    assert f"{WINDOW_SIZE[0]}x{WINDOW_SIZE[1]}" in steps[OFF_ORIGIN].detail


def test_no_displays_at_all_is_a_skip_not_a_pass():
    steps = plan_scenarios([])

    assert all(step.status == SKIP for step in steps)
    assert "no displays to enumerate" in _by_name(steps)[NEGATIVE].detail


# ---------------------------------------------------------- window placing
def test_the_window_is_placed_inside_the_work_area_with_a_margin():
    display = _display(r"\\.\DISPLAY1", (100, 50, 1700, 950), primary=True)

    left, top = window_position(display)

    assert (left, top) == (220, 170)
    assert left + WINDOW_SIZE[0] <= 1700
    assert top + WINDOW_SIZE[1] <= 950


def test_a_display_that_cannot_hold_the_window_returns_none():
    assert window_position(_display(r"\\.\DISPLAY1", (0, 0, 400, 300))) is None


# --------------------------------------------------------- click comparison
@pytest.mark.parametrize(
    ("expected", "measured", "status"),
    [
        ((500, 400), (500, 400), PASS),
        ((500, 400), (502, 401), PASS),
        ((500, 400), (503, 400), FAIL),
        ((500, 400), (500, 300), FAIL),
        ((-1500, 300), (-1499, 301), PASS),
    ],
)
def test_the_received_click_must_match_the_sent_point(expected, measured, status):
    got, error, detail = verify_click(expected, measured)

    assert got == status
    assert f"off by {error} px" in detail


# --------------------------------------------------------------- the report
def _report(*steps: Step, go: bool) -> SmokeReport:
    return SmokeReport(displays=[], steps=list(steps), go=go)


def test_a_dry_run_is_never_reported_as_a_pass():
    report = _report(Step(OFF_ORIGIN, PLAN, "would click"), go=False)

    assert report.overall == DRY_RUN
    assert "dry-run: nothing was clicked" in "\n".join(report.lines())


def test_a_dry_run_with_every_scenario_skipped_is_still_a_dry_run():
    report = _report(Step(OFF_ORIGIN, SKIP, "no display"), go=False)

    assert report.overall == DRY_RUN


def test_all_skips_after_go_is_a_skip():
    report = _report(Step(OFF_ORIGIN, SKIP, "no display"), go=True)

    assert report.overall == SKIP


def test_a_failure_dominates_a_pass():
    report = _report(Step(OFF_ORIGIN, PASS, "landed"), Step(SCALED, FAIL, "off"),
                     go=True)

    assert report.overall == FAIL


def test_one_pass_after_go_is_a_pass():
    report = _report(Step(OFF_ORIGIN, PASS, "landed"), Step(SCALED, SKIP, "100%"),
                     go=True)

    assert report.overall == PASS


def test_the_json_report_keeps_the_numbers():
    step = Step(OFF_ORIGIN, PASS, "landed", window_position=(220, 170),
                target_in_client=(210, 150), expected_screen=(529, 409),
                measured_screen=(529, 409), error=0, scale=130)
    payload = SmokeReport(steps=[step], go=True).to_dict()

    assert payload["overall"] == PASS
    assert payload["steps"][0]["expected_screen"] == [529, 409]
    assert payload["steps"][0]["error_px"] == 0


# ------------------------------------------------------- the injectable run
def test_run_accepts_an_injected_display_list_and_stays_dry():
    displays = [_display(r"\\.\DISPLAY1", (0, 0, 1920, 1080), primary=True)]

    report = run(go=False, displays=displays)

    assert report.go is False
    assert report.overall == DRY_RUN
    assert [step.status for step in report.steps].count(PLAN) >= 1
    assert report.displays == displays
