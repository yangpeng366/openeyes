# Browser task bench — measured results (2026-11-28)

The contract is `docs/browser-task-bench-spec.md`; this file records what the
bench actually measured on this machine, so the numbers can be argued with
instead of retold.

## The command

```
python benchmarks/bench_browser_task.py --suite core,extra --repeat 5 \
    --out .\_bench_out_r5
```

`--repeat 5` runs every `(task, strategy)` pair five times and adds
`elapsed_p50_s`, `elapsed_p90_s` (nearest-rank, no interpolation) and
`success_rate` (PASS runs / judged runs; SKIP runs are not judged).

Outcome: `bench_verdict = PASS`, `repeat = 5`, `control_all_failed = true`,
`pre_state_ok = true`, `errors = []`.

## Per strategy

| strategy | tasks | pass | fail | skip | calls | stdout bytes | elapsed | p50 | p90 | success |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verbs | 7 | 3 | 1 | 3 | 32 | 51 472 | 22.36 s | 1.34 s | 14.62 s | 0.75 |
| code | 7 | 7 | 0 | 0 | 7 | 4 987 | 4.86 s | 0.69 s | 0.71 s | 1.00 |
| control | 7 | 0 | 7 | 0 | 7 | 1 939 | 4.71 s | 0.67 s | 0.71 s | 0.00 |

`calls` are agent-facing CLI invocations; stdout bytes are a **context-cost
proxy, not tokens**.

## Per task (mean of 5 repetitions)

| task | strategy | calls | stdout bytes | mean s | p90 s | success | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| checklist-20 | verbs | 21 | 34 299 | 14.74 | 15.09 | 1.00 | PASS |
| checklist-20 | code | 1 | 924 | 0.68 | 0.71 | 1.00 | PASS |
| form-submit | verbs | 5 | 4 283 | 3.43 | 3.54 | 1.00 | PASS |
| form-submit | code | 1 | 643 | 0.75 | 0.89 | 1.00 | PASS |
| read-rows | verbs | 0 | 0 | 0.00 | 0.00 | — | SKIP |
| read-rows | code | 1 | 1 099 | 0.72 | 0.86 | 1.00 | PASS |
| overlay-link | verbs | 2 | 1 215 | 1.37 | 1.48 | 0.00 | FAIL |
| overlay-link | code | 1 | 580 | 0.68 | 0.71 | 1.00 | PASS |
| shadow-toggle | verbs | 1 | 3 | 0.69 | 0.71 | — | SKIP |
| shadow-toggle | code | 1 | 580 | 0.68 | 0.71 | 1.00 | PASS |
| virtual-list | verbs | 2 | 11 669 | 1.44 | 1.56 | 1.00 | PASS |
| virtual-list | code | 1 | 580 | 0.68 | 0.70 | 1.00 | PASS |
| iframe-nested | verbs | 1 | 3 | 0.67 | 0.71 | — | SKIP |
| iframe-nested | code | 1 | 581 | 0.68 | 0.71 | 1.00 | PASS |

Control rows: 7 tasks, 7 FAIL, 0 PASS, on all five repetitions.

## What the numbers say

1. **The fixed menu pays per action, the code route pays per task.**
   `checklist-20` is 21 calls / 34 KB / 14.7 s against 1 call / 0.9 KB / 0.7 s:
   about 21× the calls, 37× the bytes the agent must read, 22× the wall clock.
   The gap is linear in the number of actions, so it grows with the list.
2. **`browser scan` is the expensive read.** `virtual-list` needs one scan and
   one click (2 calls) yet costs 11.7 KB — nearly all of it is the scan
   reporting 21 rows. A route that scans a big page pays for the whole page
   whether it needs it or not.
3. **The capability gaps are real, not slow paths.** `read-rows` is SKIP for
   verbs (the menu has no read verb) and `shadow-toggle` / `iframe-nested` are
   SKIP because the scan reports 0 interactive elements inside a shadow root or
   a nested browsing context. These are SKIP with a reason, never FAIL: a
   skipped route does not count as a pass.
4. **The bench can fail a route.** `overlay-link` is verbs FAIL: the scan
   reports the link's centre, the click lands on the transparent overlay, the
   page never activates. The code route clicks the element itself and passes.
   This is the row that proves the pass column is not free.
5. **The control never passes** (7/7 FAIL × 5 repetitions), and every page was
   proven "not yet done" before the route ran (`pre_state_ok = true`).

## The measured correction adopted this round

The first run of the extra suite reported `shadow-toggle` and `iframe-nested`
as verbs **FAIL** (scan: 0 interactive elements, then `browser click` exit 4
"could not resolve a browser target"). That was the bench violating its own
rule 4.5 ("a strategy that cannot express a task is SKIP, never FAIL"): the
route never addressed the element, so there was nothing to fail. The runner now
stops at the scan, spends no blind click, and records SKIP with the scan
evidence. `tests/test_bench_browser_task.py` pins that behaviour.

## Reproducing

* Fresh `--out` directory required: the receipt writer refuses to overwrite
  evidence (`refusing to overwrite existing evidence ... pass --force`).
* One visible Edge on port 9333, killed by the bench; one fixture tab at a time,
  every call carrying `--url-contains bench-<token>`.
* Receipts from this round live outside the repo (`_bench_out_r5/`); the
  numbers above are what they contain.
