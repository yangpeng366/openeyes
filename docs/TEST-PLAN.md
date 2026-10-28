# Test plan

The discipline this repository follows, borrowed from the harnesses that
drive other software: **the plan comes before the test code**, the unit
layer and the real-backend layer are kept apart, and a test verifies the
*product* rather than the fact that a function returned.

A test that only checks "it returned" is what let the AnyVPN keepalive
report success for months while the session died. The rule that would
have caught it is the one below: **verify the state, not the call**.

## 1. The plan first

Before writing a test, write down:

| Field | What it says |
| --- | --- |
| **Behaviour** | What must be true, in one sentence, from the user's side |
| **Layer** | `unit` (no desktop) or `live` (needs a real window / process / network) |
| **How it is proved** | The observable fact that settles it - not "no exception" |
| **Risk** | What breaks if this regresses |

The plan lives in the commit message or the pull request. The test code
follows it; when the plan and the tests disagree, one of them is wrong
and the disagreement is the finding.

## 2. Two layers, never mixed

`unit` - no desktop, no network, no timing. These run everywhere and
must be fast and deterministic. A unit test may stub the desktop, but it
may not depend on one.

`live` - needs a real window, a real process, a real browser or a real
network. These prove the contract that actually matters and they are
**skipped with a named reason** when their prerequisite is absent. A skip
is not a pass; the CI logs both.

```python
pytestmark = pytest.mark.skipif(
    shutil.which("wsl") is None,
    reason="wsl not on PATH; the Lightpanda backend needs WSL2 + Docker.",
)
```

## 3. Prove the product

| Instead of | Assert |
| --- | --- |
| "the function returned" | the state changed |
| "the command exited 0" | the output file exists and has the expected content |
| "no exception was raised" | the error message names the cause |
| "the label changed" | the **business** state changed (a counter went back up) |

The rule that keeps biting: **a live label is not a state**. A countdown
label ticks every second on its own, so "the name differs" is always
true and therefore proves nothing.

## 4. A delivered action is not an effective action

This repository's own rule, and the one the tests enforce most often:

- the action is *delivered* - the pointer arrived, the message was sent;
- the action is *effective* - the business state changed.

Only the second one may be reported as success. A test that asserts on
the first is testing the wrong thing.

## 5. Interference is measured, not assumed

A loop that lives on someone's desktop must be judged on two numbers:
false positives (successes that did not happen) and interference (cursor
moves, focus theft). Both are attributed to the **automation**, not to the
desktop - the user's own cursor keeps moving, and the focus may already
sit on the target.

## 6. What the gate runs

`examples/quality-gate.ps1` (and the same four steps in CI):

1. `ruff check .` - lint;
2. `python -m pytest tests/ -q` - the tests;
3. `python examples/bom-sweep.py` - UTF-8, no BOM;
4. `python examples/mcp-stdio-probe.py` - the MCP contract over real stdio.

A push is ready when the gate exits 0. **A skip is not a pass**, and a
known-flaky test is deselected in the patrol invocation with the reason
recorded - never quietly repaired in the same commit.

## 7. Adding a test

1. Write the plan (section 1).
2. Put it in `unit` unless it genuinely needs a desktop; a `live` test
   must skip with a reason that says what is missing.
3. Assert the **product**: the observable fact, not the call.
4. For anything that acts on a desktop, also assert the interference:
   that the user's cursor did not move and the focus was not stolen.
5. Run the gate. A red gate is not a push.
