# Browser task bench — spec (2026-11-24)

**Status:** contract for the implementation. Every agent reads this file; it is
the only source of truth for the API shapes. Do not change it — ask the lead.

## 1. The question

For the same browser task, what does each route cost the agent?

| strategy | route |
| --- | --- |
| `verbs` | the fixed menu: `browser scan` → `browser click --name-contains` → `browser type` |
| `code` | the code-first escape hatch: one `browser exec --code` that loops |
| `control` | a do-nothing strategy. It must FAIL every task. |

The bench measures the **harness cost of the two routes**, not code quality and
not model quality: no model is in the loop. The code each strategy runs is
precomputed, so the comparison is "one call that loops" against "N calls that
cannot loop".

## 2. Non-goals

* Not an LLM benchmark, not a latency benchmark of Edge.
* Not a replacement for the four browser acceptance probes
  (`browser-{scan,click,type,shot}-acceptance.py`); those judge one verb, this
  judges a task across routes.
* Not a place for a second verdict vocabulary. `openeyes.core.verdict` owns
  PASS / FAIL / SKIP and the exit codes; `examples/_acceptance_verdict.py` owns
  the receipt helpers. Reuse both.

## 3. Metrics (and their honest limits)

Per `(task, strategy)`:

| metric | meaning |
| --- | --- |
| `calls` | agent-facing CLI invocations **by the strategy**. Setup and judgement calls are counted separately and never inflate this number. |
| `agent_output_bytes` | bytes of stdout the agent had to read. This is a **context-cost proxy, not tokens** - the receipt must not claim a token count. |
| `elapsed_s` | wall clock of the strategy only. |
| `verdict` | PASS / FAIL / SKIP, judged from the page state *after* the strategy ran. |

## 4. Honesty rules (non-negotiable)

1. **The verdict comes from an independent judgement call**, never from what the
   strategy says it did. The strategy has no way to report success.
2. **A `control` run that passes any task means the bench is broken.** The bench
   verdict is then FAIL (exit 2), loudly, with the offending task named.
3. Setup (open the page) and judgement (read the expectation) calls are counted
   separately: `setup_calls`, `judgement_calls`.
4. Every task's page must start in a "not yet done" state. A task whose page
   already satisfies the expectation before the strategy runs is a **bench bug** -
   the bench verdict must be FAIL, not PASS.
5. A strategy that cannot express a task is **SKIP** with a reason - never
   FAIL ("I cannot") and never PASS. `SKIP` beats `PASS` in the combine rule,
   so a skipped route cannot fake a win.

## 4a. Errata (measured 2026-11-24, lead)

* **`browser scan` has no `--json` of its own.** The flag is global and must come
  **before** the subcommand: `eyes --json browser scan --port 9334`.
  `eyes browser scan --json` exits 2 with "No such option: --json".
  (`browser click`, `browser exec`, `browser tabs` do accept `--json` at the end.)
* A checkbox's accessible name in the scan output is its **`id`** (`c1`), while
  the `<label>` carries the human text (`item-01`). The verbs route addresses
  the **label** by its text; clicking a label's centre toggles the associated
  input (measured: clicking `item-01` and `item-03` left the page with 2 of 3
  boxes checked). So fixture pages must give every checkbox a `<label for=...>`
  whose text is the unique name the route will use.

## 4b. Review-driven hardening (adopted 2026-11-24, lead)

An independent review asked "where can this bench go green without meaning
anything". Five gaps were real and are now part of the contract:

1. **pre-state guard.** Right after setup and **before any strategy runs**, the
   runner evaluates `expect_js` once. If the result already equals `expected`,
   the task's page starts done: that is a bench bug - `bench_verdict` becomes
   FAIL (exit 2), the offending task is named, and the run stops. This is what
   stops a `control` from "naturally" passing.
2. **control-pass hard guard.** Any task whose `control` verdict is not FAIL
   forces `bench_verdict` FAIL (exit 2), naming the task.
3. **SKIP is not a pass.** The summary carries `skip_count` and the reason per
   strategy, and says in words that a skipped route does not count towards
   pass/fail. A bench where every route is SKIP is SKIP, not PASS.
4. **fixture server binding.** Bind `127.0.0.1` explicitly, fail loudly (exit
   4) when the port is taken - never fall back silently to another port, or the
   token in the URL will not match the tab.
5. **cleanup on interrupt.** Register the interrupt handler and clean up in a
   `finally`: kill the Edge this run started, stop the fixture server, remove
   the temporary profile. An interrupted run exits **130** and the receipt says
   `interrupted: true` - never 0, never 1.

Also adopted:

* The receipt names the per-run token (`bench_token`) so a run can be traced,
  and the runner verifies the loaded page really carries that token before it
  judges anything.
* `metric_notes` states "`agent_output_bytes` is bytes, NOT a token count", and
  the JSON carries `"x-not-tokens": true` next to it.
* `expect_js` must read **ground truth produced by the element itself** (its
  own activation handler ran), never a value the strategy itself could have
  written. A strategy must not be able to fake the thing it is measured on.

## 4c. Adopted 2026-11-28: SKIP enforcement in the runner, and `--repeat`

Two additions, both measured on a real Edge.

1. **Rule 4.5 is now enforcement, not prose.** The first extra-suite run
   reported `shadow-toggle` and `iframe-nested` as verbs FAIL: `browser scan`
   returned 0 interactive elements and the following `browser click
   --name-contains` exited 4 ("could not resolve a browser target"). The route
   never addressed the element, so there was nothing to fail - that was the
   bench violating its own rule. The verbs route now stops when the scan shows
   no element matching a target, spends no blind click, and the row is SKIP
   with the scan evidence in `reason` (the calls it did spend stay in `calls`).
   Pinned by `tests/test_bench_browser_task.py`.
2. **`--repeat N` (default 1) for success rate and P90.** Capability
   differences are binary; cost is a distribution. With `N > 1` each
   `(task, strategy)` runs N times and the receipt row carries `repeat`,
   `elapsed_p50_s`, `elapsed_p90_s` (nearest-rank, no interpolation),
   `success_rate` (PASS runs / judged runs, SKIP not judged) and a compact
   `detail.runs`. Aggregate verdict across repetitions: any FAIL -> FAIL, every
   SKIP -> SKIP, and a mix -> SKIP (repetitions that disagree are not a pass).
   `detail.runs` replaces `detail.calls_detail`, which is identical in argv
   apart from the per-run token. `--repeat 1` keeps the receipt exactly as in
   section 5.3. Measured numbers: `docs/browser-task-bench-results.md`.

## 5. API contract

### 5.1 `benchmarks/_browser_task_suite.py` (core suite)

```python
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

def load_tasks() -> list[Task]:
    """Every task, in a stable order. Ids are unique and stable."""
```

`benchmarks/_browser_task_suite_extra.py` exposes the **same** `Task` and
`load_tasks()` (extra widget classes: shadow DOM, virtual list, iframe).

### 5.2 `benchmarks/bench_browser_task.py` (the runner)

```
python benchmarks/bench_browser_task.py [--port 9333] [--suite core,extra]
                                         [--tasks id,id] [--strategies verbs,code,control]
                                         [--out DIR] [--json] [--no-launch]
```

* `--port`: CDP port (default 9333). `--no-launch`: use a browser that is
  already listening; do not kill it.
* `--suite`: comma list of `core` / `extra`.
* `--out`: directory for the JSON receipt. Use `_resolve_out_dir` +
  `_write_receipt_atomic` from `examples/_acceptance_verdict.py`; a stale
  receipt in that directory must never be read as this run's.
* `--json`: print the receipt object on stdout.

Runner rules:

* Visible browser only (no headless). Launch with
  `openeyes.backends.cdp.launch_edge(port=..., seed=False, headless=False)`;
  kill exactly what you started, and only that.
* Serve the fixtures over HTTP with `_start_fixture_server` - Edge discards
  `file://` tabs opened through CDP, so `file://` is not an option.
* **Exactly one fixture tab is open at a time.** Every CLI call carries
  `--url-contains <token>` where the token is unique per run, and the page URL
  carries the same token (`?bench=<token>`), so a call can never hit the wrong
  tab.
* Setup per run: open the tab, load the page. Judgement per run: one
  `browser exec --code <expect_js> --go --json` and compare `result` to
  `expected`.
* The verbs route reads `browser scan --json`, picks elements by accessible
  name, then clicks one by one. The code route is one `browser exec --code ... --go`.
* The control route is `browser exec --code "void 0" --go --json`.

### 5.3 Receipt shape

```json
{
  "bench": "browser_task",
  "generated_at": "2026-11-24T00:00:00+08:00",
  "port": 9333,
  "suites": ["core"],
  "metric_notes": {
    "calls": "agent-facing CLI invocations by the strategy; setup and judgement excluded",
    "agent_output_bytes": "bytes of stdout the agent had to read - a context-cost proxy, NOT tokens",
    "elapsed_s": "wall clock of the strategy only",
    "verdict": "PASS/FAIL/SKIP judged from the page state by an independent call"
  },
  "tasks": [
    {"task": "checklist-20", "strategy": "verbs", "calls": 41,
     "agent_output_bytes": 12345, "elapsed_s": 12.3,
     "setup_calls": 1, "judgement_calls": 1,
     "verdict": "PASS", "reason": "", "detail": {}}
  ],
  "summary": {
    "by_strategy": {"verbs": {"tasks": 4, "pass": 3, "fail": 0, "skip": 1,
                               "calls": 90, "agent_output_bytes": 34567, "elapsed_s": 40.1}},
    "control_all_failed": true,
    "pre_state_ok": true
  },
  "bench_verdict": "PASS"
}
```

`bench_verdict` is FAIL when any control passed, when any task's page already
satisfied its expectation before the run, or when a run raised an unexpected
error; SKIP when nothing could be judged; PASS otherwise. The process exit code
comes from `openeyes.core.verdict.EXIT_CODES`.

## 6. Fixture pages

* Self-contained HTML, no external asset, no network, no `alert`/`prompt`.
* Deterministic: no randomness, no timers.
* State starts "not yet done" (a counter at 0, a result slot empty).
* Every clickable control has a **distinct accessible name** (`item-01` ...),
  because the verbs route addresses controls by name.
* Put the page under `examples/bench_pages/`, serve it over HTTP.

Core suite (lead-assigned, 4 tasks):

| task | page | what it proves |
| --- | --- | --- |
| `checklist-20` | `checklist.html` | 20 checkboxes; the verbs route needs 1 scan + 20 clicks, the code route 1 call |
| `form-submit` | `form.html` | 3 inputs + submit; both routes can do it, the costs differ |
| `read-rows` | `table.html` | 50 rows of text; the fixed menu has **no page-text read verb**, so the verbs route is SKIP with a reason |
| `overlay-link` | `overlay.html` | a transparent overlay covers the link: the verbs route clicks the reported centre and **FAILS**, the code route clicks the element and PASSES. This is the case that proves the bench can fail. |

Extra suite (lead-assigned, 3 tasks): shadow DOM, virtual list (only visible rows
in the DOM), iframe (nested browsing context).

## 7. Definition of done

* `python -m ruff check benchmarks examples tests` clean.
* `python examples/bom-sweep.py` reports 0 hits.
* `python benchmarks/bench_browser_task.py --json` runs against a real visible
  Edge and prints a receipt whose `bench_verdict` is PASS, with the control
  column failing on every task.
* The receipt says plainly that `agent_output_bytes` is not tokens.
