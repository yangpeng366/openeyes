# `eyes browser exec` — the code-first escape hatch

## Why this exists

OpenEyes hands a model a **fixed menu**: `scan`, `click`, `type`, `shot`. That menu is
the whole point of the project — structured detection instead of pixel-guessing — but it
is also a ceiling. A loop over 200 rows is 200 menu calls; a widget nobody modelled is
not on the menu at all.

Tabbit spent a year and a half building that kind of harness (DOM compressed into a
hundred tags, invisible elements trimmed, hundreds of model x harness combinations
tested) and then watched it lose to something trivial:

> Give the agent a terminal and code execution. It will write the loop itself.

Their write-up calls it *The Bitter Lesson*: knowledge baked into a harness helps in the
short run and turns into a bottleneck in the long run. The same turn of the screw shows
up in the ecosystem — Browser Use abandoned pre-wrapped tools for "give the Coding Agent
the CDP socket and let it write its own control code"; Google built APC (a text dump of
the rendered page) and still did not get the win; Claude and Gemini both added *batch*
tools to squeeze more out of the fixed-menu shape.

So OpenEyes keeps its structured verbs — they are still the cheap, reliable tier — and
adds **one** way out of the menu: the caller writes the code.

## What it does

```powershell
# a one-liner: the page's own value, returned as JSON
eyes browser exec --code "document.title" --go --json

# a script with loops: one call instead of N scan/click/type rounds
eyes browser exec --file collect.js --go --json

# a dry run: the receipt names the payload and its sha1, the browser is untouched
eyes browser exec --code "document.title" --json
```

The same verb exists on the MCP server as `browser_exec`.

Contract (also in `docs/capability-contract.md`):

* exactly one of `--code` / `--file` (MCP: `code` / `code_file`);
* dry-run by default: `executed: false`, `dry_run: true`, `would_execute: true`,
  `code_chars`, `code_sha1`, `preview`. A dry run does **not** touch the browser —
  it connects only when `--url-contains` has to be resolved, and then fails closed;
* `--go` (`go: true` on MCP) runs the code and returns its value by value;
  `dry_run: true` beats `go`, so a dry run can never execute by accident;
* a page error is reported with its cause (`page JavaScript failed: ...`), never
  swallowed; a mismatched `--url-contains` fails closed with exit code 4.

Both protocols work: CDP (Chromium/Edge) via `Runtime.evaluate`, Firefox WebDriver BiDi
via `script.evaluate`.

## Measured on a real Edge window (2026-11-24)

```text
eyes browser launch --url https://example.com --port 9333
eyes browser exec --code "document.title" --port 9333 --go --json
  -> "result": "Example Domain"          (visible Edge, real page)

eyes browser exec --file oe_loop.js --port 9333 --go --json
  -> "result": "P: ... | P: ... | P: ... | P: ... | P: ... | P: ... | P: ..."   (7 paragraphs, one call)

eyes browser exec --code "1" --go --url-contains nowhere --json
  -> exit 4, "no page target matched url_contains='nowhere'; available URLs: 'https://example.com/'"
```

The loop case is the point: seven paragraphs came back in one call, where the fixed menu
would have needed a scan and a read per item.

## What it is not

* It is not a Playwright replacement and it does not add one. The code runs **in the
  page**; it cannot install a driver or drive another browser.
* It carries no state between calls. A loop that must survive across calls belongs in
  one script (`--file`), not in several `exec` rounds.
* It is a **high-risk** surface: page JavaScript can do anything the page can do. The
  dry-run default exists for that reason — read the payload before you run it.
* It does not observe the user's real browser session unless the caller points it at a
  port it launched (`eyes browser launch` seeds a disposable profile).

## Follow-ups worth doing next

1. **A task-level bench.** Tabbit reports success rate, input tokens and P90 latency
   over 25 tasks x 3 runs. OpenEyes has `benchmarks/bench_anchor_frame.py` and
   `bench_opencv.py` but nothing that measures *task* success. The interesting
   comparison for us is fixed-menu vs code-first on the same task set.
2. **Batch with an explicit stale-state warning.** Gemini's batch tools and Claude's
   `browser_batch` save round trips but silently build on an outdated view of the page.
   If we add batching, the receipt has to say which state each action saw.
3. **Cheap perception, expensive planning.** A local small model can classify page
   state far faster than a general model (0.76 s vs 8.54 s in their measurements) while
   being no better at open-ended work. Our `decision/` tier and the anchor probe are
   already that shape; keep them bounded and leave the planning to the caller.
