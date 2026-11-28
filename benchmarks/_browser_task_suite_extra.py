"""Extra browser-task suite: shadow DOM, virtual list, nested iframe.

The core suite (``benchmarks/_browser_task_suite.py``) covers checkbox / form /
table / overlay. This module covers the three widget classes where the fixed
verb menu is most likely to fumble, so the bench sees more than one shape of
failure.

The contract is the spec, verbatim: ``docs/browser-task-bench-spec.md`` §5.1 -
one frozen ``Task`` dataclass with the same field names and defaults, and
``load_tasks()`` returning every task in a stable order with stable ids. The
dataclass is defined here rather than imported so this module loads on its own;
if the two definitions ever drift, the core suite is the one to follow.

Why these three:

* ``shadow.html`` - the toggle exists only inside an open shadow root. The DOM
  probe behind ``browser scan`` walks ``document.querySelectorAll`` on the top
  document, so the control has no accessible name the verbs route can address.
* ``virtual-list.html`` - 500 rows, of which only the 20-row window is ever in
  the DOM. The total is a page fact (``body[data-total]``), never something a
  strategy can count off the rendered rows.
* ``iframe.html`` - the control lives in a same-origin child document. The probe
  never crosses the frame boundary.

Every page starts "not yet done" (spec §4.4) and publishes the judged fact on a
single ``data-*`` attribute, so each ``expect_js`` is one short expression a
human can check by eye (spec §4.1: the verdict must never come from what the
strategy claims it did).

``requires`` uses the runner's own capability vocabulary only - ``click`` /
``type`` / ``read_text`` (``VERBS_CAPABILITIES`` / ``CODE_CAPABILITIES`` in
``benchmarks/bench_browser_task.py``). A tag outside that vocabulary makes both
routes SKIP, which would measure nothing, so the widget classes are stated in
``note`` instead: the verbs route is gated on its scan seeing the control, not
on a capability tag it has no name for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Task:
    id: str
    page: str                 # file name under examples/bench_pages/
    expect_js: str            # JS expression, evaluated in the page AFTER the run
    expected: Any             # JSON-comparable expected value
    targets: tuple[str, ...] = ()   # element names the verbs route clicks
    input_text: str = ""              # text the verbs route types
    requires: tuple[str, ...] = ()   # capability tags, e.g. ("read_text",)
    note: str = ""


#: The only capability tags the runner understands.
_CLICK = ("click",)


def load_tasks() -> list[Task]:
    """Every extra-suite task, in a stable order. Ids are unique and stable."""
    return [
        Task(
            id="shadow-toggle",
            page="shadow.html",
            # the shadow tree mirrors its own state onto the body; reading the
            # root from here would work too, but one attribute is easier to check
            expect_js="document.body.dataset.shadowState",
            expected="on",
            targets=("shadow-toggle",),
            requires=_CLICK,
            note=(
                "the only control is inside an open shadow root of #gate; a scan "
                "of the top document returns no element by that name, so the "
                "verbs click cannot land, while one exec pierces the root and "
                "flips body[data-shadow-state] from 'off' to 'on'"
            ),
        ),
        Task(
            id="virtual-list",
            page="virtual-list.html",
            # the total is a page fact (data-total), not something to count:
            # the DOM only ever holds the 20-row window
            expect_js="document.body.dataset.total + ':' + document.body.dataset.picked",
            expected="500:row-007",
            targets=("row-007",),
            requires=_CLICK,
            note=(
                "500 rows, 20 in the DOM at any offset: a scan reports the window, "
                "never the list, so the total comes from body[data-total] and not "
                "from counting rendered rows; the clicked row is inside the "
                "initial window, because a row further down would need a scroll "
                "verb the fixed menu does not have and the precomputed code route "
                "does not carry either"
            ),
        ),
        Task(
            id="iframe-nested",
            page="iframe.html",
            # read the child document through the frame element's stable id
            expect_js=(
                "document.getElementById('bench-frame')"
                ".contentDocument?.body?.dataset?.state || ''"
            ),
            expected="done",
            targets=("iframe-confirm",),
            requires=_CLICK,
            note=(
                "the control lives in a same-origin child document (written by the "
                "page, so it stays scriptable); a scan of the top document never "
                "crosses the frame boundary, while one exec reaches the child body "
                "and moves its data-state from 'pending' to 'done'"
            ),
        ),
    ]
