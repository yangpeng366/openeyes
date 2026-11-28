"""Core task suite for ``benchmarks/bench_browser_task.py``.

The contract is ``docs/browser-task-bench-spec.md`` (§5.1 for the shape,
§6 for the four pages); this module only *describes* tasks.  It never
drives a browser, never reads a receipt and never judges anything - the
runner does all three.

Every task here is one row of the spec's core suite:

    checklist-20   20 checkboxes, all of them must end up checked
    form-submit    3 labelled inputs + a submit button, result slot filled
    read-rows      50 rows of text, no page-text read verb in the fixed menu
    overlay-link   a transparent scrim covers the link, so a click at the
                   reported centre never reaches it

``expect_js`` is an **expression**, evaluated by the runner through
``browser exec --code <expect_js> --go --json`` (``Runtime.evaluate`` with
``returnByValue``), and its value is compared with ``expected``.  Each
expression is therefore side-effect free, a single JS expression, and
returns a JSON value (number / string / boolean).

``targets`` are the *accessible names* the verbs route clicks, in order.
The pages give every control an id, a ``<label for=...>`` text and - where
the control is a button - a text label that are all the same string, so a
name match can never be ambiguous and the label-click path of the errata
(§4a) toggles exactly the intended input.

The pages start in the "not yet done" state (counter at 0, result slot
empty, no ``data-clicked`` marker): an expectation that already held before
the strategy ran is a bench bug, not a PASS, and that is only detectable if
the fixtures really do start empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Directory the runner's fixture server must expose (``examples/bench_pages``).
PAGES_DIR = Path(__file__).resolve().parents[1] / "examples" / "bench_pages"

#: The 20 checkbox names, in click order; also the ids and label texts.
CHECKLIST_NAMES: tuple[str, ...] = tuple(f"item-{n:02d}" for n in range(1, 21))

#: The payload cell of each table row, in DOM order.
ROW_PAYLOADS: tuple[str, ...] = tuple(f"alpha-{n:02d}" for n in range(1, 51))

#: Text the verbs route types into each of the three form fields.
FORM_TEXT = "alpha"

_CHECKED_COUNT_JS = "document.querySelectorAll('#checklist input[type=checkbox]:checked').length"
_FORM_RESULT_JS = "document.getElementById('result').textContent"
_READOUT_JS = "document.body.dataset.readout || ''"
_LINK_CLICKED_JS = "document.getElementById('overlay-link').dataset.clicked === '1'"


@dataclass(frozen=True)
class Task:
    """One benchmark task: a page, an expectation, and how to reach it."""

    id: str
    page: str
    expect_js: str
    expected: Any
    targets: tuple[str, ...] = ()
    input_text: str = ""
    requires: tuple[str, ...] = ()
    note: str = ""


def load_tasks() -> list[Task]:
    """Every task, in a stable order. Ids are unique and stable."""
    return [
        Task(
            id="checklist-20",
            page="checklist.html",
            expect_js=_CHECKED_COUNT_JS,
            expected=20,
            targets=CHECKLIST_NAMES,
            note=(
                "20 checkboxes; verbs needs 1 scan + 20 clicks, code needs 1 call. "
                "Each label text equals its checkbox id, so the label-click path "
                "toggles the intended box."
            ),
        ),
        Task(
            id="form-submit",
            page="form.html",
            expect_js=_FORM_RESULT_JS,
            expected="|".join([FORM_TEXT] * 3),
            targets=("field-one", "field-two", "field-three", "submit-form"),
            input_text=FORM_TEXT,
            note=(
                "Click field-one/field-two/field-three, type the same text into each, "
                "then click submit-form; the page writes the three values joined by "
                "'|' into #result. #result is empty until then."
            ),
        ),
        Task(
            id="read-rows",
            page="table.html",
            expect_js=_READOUT_JS,
            expected="ok:" + str(len(ROW_PAYLOADS)),
            targets=("readout", "verify-readout"),
            requires=("read_text",),
            note=(
                "50 rows; the fixed menu has no page-text read verb, so the verbs "
                "route is SKIP (reason: no read-text verb), never FAIL and never "
                "PASS. Code route: read the 50 payload rows, type them into "
                "#readout one per line, then click verify-readout - the page "
                "compares the readout with its own rows and publishes ok:50 on "
                "body[data-readout], so the judged value is the page's verdict on "
                "the transcription, not a value the route wrote itself."
            ),
        ),
        Task(
            id="overlay-link",
            page="overlay.html",
            expect_js=_LINK_CLICKED_JS,
            expected=True,
            targets=("overlay-link",),
            note=(
                "A transparent scrim covers the link's box exactly, so the topmost "
                "element at the reported centre is the scrim: a coordinate click "
                "leaves data-clicked unset and the verbs route FAILS. Code route: "
                "click the element itself (document.getElementById('overlay-link')"
                ".click()) and the listener sets data-clicked='1'."
            ),
        ),
    ]


__all__ = [
    "CHECKLIST_NAMES",
    "FORM_TEXT",
    "PAGES_DIR",
    "ROW_PAYLOADS",
    "Task",
    "load_tasks",
]
