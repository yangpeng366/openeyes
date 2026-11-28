# Browser task bench — measured results (2026-11-28, `--warmup 1`)

The contract is `docs/browser-task-bench-spec.md`; this file records what the
bench actually measured on this machine, so the numbers can be argued with
instead of retold.

## The command

```
python benchmarks/bench_browser_task.py --suite core,extra --repeat 5 \
    --warmup 1 --out .\bench-out-w3
```

`--repeat 5` runs every `(task, strategy)` pair five times and adds
`elapsed_p50_s`, `elapsed_p90_s` (nearest-rank, no interpolation) and
`success_rate` (PASS runs / judged runs; SKIP runs are not judged).
`--warmup 1` spends one extra run per pair **before** those five and throws it
away: it is recorded in `detail.warmup_runs` and counted in no metric, so the
cold first run (cold CLI, cold renderer, empty page cache) cannot sit in the
p90.

Outcome: `bench_verdict = PASS`, `repeat = 5`, `warmup = 1`,
`control_all_failed = true`, `pre_state_ok = true`, `errors = []`, and zero
Edge processes left behind on the bench profile after the run.

## Per strategy

| strategy | tasks | pass | fail | skip | calls | stdout bytes | elapsed | p50 | p90 | success |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| verbs | 7 | 3 | 1 | 3 | 32 | 51 472 | 22.65 s | 1.35 s | 14.83 s | 0.75 |
| code | 7 | 7 | 0 | 0 | 7 | 4 987 | 4.94 s | 0.70 s | 0.76 s | 1.00 |
| control | 7 | 0 | 7 | 0 | 7 | 1 939 | 4.83 s | 0.68 s | 0.77 s | 0.00 |

`calls` are agent-facing CLI invocations; stdout bytes are a **context-cost
proxy, not tokens**. `elapsed` is the sum of the per-task means.

## Per task (mean of 5 kept repetitions, p90 of those 5)

| task | strategy | calls | stdout bytes | mean s | p90 s | success | warmup run s | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| checklist-20 | verbs | 21 | 34 299 | 14.98 | 15.27 | 1.00 | 16.09 | PASS |
| checklist-20 | code | 1 | 924 | 0.70 | 0.75 | 1.00 | 0.64 | PASS |
| form-submit | verbs | 5 | 4 283 | 3.53 | 3.60 | 1.00 | 3.78 | PASS |
| form-submit | code | 1 | 643 | 0.76 | 0.90 | 1.00 | 0.67 | PASS |
| read-rows | verbs | 0 | 0 | 0.00 | 0.00 | — | 0.00 | SKIP |
| read-rows | code | 1 | 1 099 | 0.71 | 0.76 | 1.00 | 0.69 | PASS |
| overlay-link | verbs | 2 | 1 215 | 1.37 | 1.46 | 0.00 | 1.48 | FAIL |
| overlay-link | code | 1 | 580 | 0.74 | 0.82 | 1.00 | 0.67 | PASS |
| shadow-toggle | verbs | 1 | 3 | 0.65 | 0.68 | — | 0.66 | SKIP |
| shadow-toggle | code | 1 | 580 | 0.69 | 0.80 | 1.00 | 0.68 | PASS |
| virtual-list | verbs | 2 | 11 669 | 1.45 | 1.60 | 1.00 | 1.42 | PASS |
| virtual-list | code | 1 | 580 | 0.68 | 0.72 | 1.00 | 0.70 | PASS |
| iframe-nested | verbs | 1 | 3 | 0.67 | 0.72 | — | 0.70 | SKIP |
| iframe-nested | code | 1 | 581 | 0.67 | 0.72 | 1.00 | 0.71 | PASS |

Control rows: 7 tasks, 7 FAIL, 0 PASS, on all five repetitions (p90 0.77 s).

## What the numbers say

1. **The fixed menu pays per action, the code route pays per task.**
   `checklist-20` is 21 calls / 34 KB / 15.0 s against 1 call / 0.9 KB / 0.7 s:
   about 21× the calls, 37× the bytes the agent must read, 21× the wall clock.
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

## What the warmup changed, and what it did not

* The cold first run is measurable: `checklist-20`/verbs took **16.09 s** as
  the warmup and **14.98 s** as the mean of the five kept runs — about 1.1 s
  (7 %) of the first run is cold start. `form-submit`/verbs shows the same
  shape (3.78 s cold vs 3.53 s kept). The cheap code/control rows show no
  usable difference (≈0.01 s), which is what "little to warm up" looks like.
* The p90 did **not** move much: verbs p90 was 14.62 s in the earlier
  `--repeat 5` run with no warmup and is 14.83 s here. With five samples the
  nearest-rank p90 is the maximum, and the 20-click route's per-click cost
  dominates the tail; the cold start is a bias, not the tail. So `--warmup`
  removes a known bias from the mean, and must not be sold as a p90 fix.
* The honest reading of the tail: `checklist-20`/verbs is ≈15 s p90 either way,
  `code`/`control` are ≈0.7–0.8 s p90 across all seven tasks, and the
  run-to-run spread on the verbs route (14.7–15.3 s across two runs) is larger
  than the cold-start effect.

## A cleanup defect found by re-running

The first `--warmup 1` attempt crashed with `ConnectionResetError` while opening
a tab, and left the Edge it had launched listening on 9333. Cause:
`_EdgeProcess.wait()` returned 0 while the pid was still alive, so the shared
`_stop_process` believed the close had worked and never escalated from
`taskkill` (no `/F`, only a close message a debugging Edge may ignore) to
`taskkill /F`. That is the bench breaking its own "kill exactly what you
started" rule, and it is exactly the kind of leftover that makes the *next*
run fail.

Fixed in this round:

* `_EdgeProcess.wait()` now raises `subprocess.TimeoutExpired` when the pid
  outlives the deadline, so the force-kill path is actually taken.
* `_stop_edge()` reports a survivor into `receipt.errors` instead of raising
  inside `finally` (a receipt must still be written, and the bench verdict must
  turn FAIL).
* Pinned by `tests/test_bench_browser_task.py` (escalation, survivor, and
  `wait` raising).

Verified after the re-run: 0 `msedge.exe` processes carry
`openeyes-bench\edge-profile-9333`.

## Reproducing

* Fresh `--out` directory required: the receipt writer refuses to overwrite
  evidence (`refusing to overwrite existing evidence ... pass --force`).
* One visible Edge on port 9333, killed by the bench; one fixture tab at a time,
  every call carrying `--url-contains bench-<token>`.
* Receipts from this round live outside the repo (`bench-out-w3/`, gitignored);
  the numbers above are what `browser-task-bench-receipt.json` contains.