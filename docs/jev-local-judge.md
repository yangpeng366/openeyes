# The local judge: a XERJ history vote in place of a hosted Jev key

The decision layer can score UI candidates without a hosted `TYPESAFE_API_KEY`
by asking a local XERJ node, which speaks the same System One wire on its
native REST port. This file records what that wire actually gives, what was
measured on it, and the thresholds that came out of the measurement.

## What the wire gives us

`POST {base}/v1/systemone` with the usual System One payload; the answer
carries `answers.target = {type, choice, confidence, probabilities}`.

Three breaks from the hosted API are documented upstream
([xerj.org/docs/llms.txt](https://xerj.org/docs/llms.txt)):

1. `model` echoes `xerj-history-vote-1`, never a Jev model name.
2. The probability is a **support share** - the winner's share of the vote
   weight among the labelled neighbours the node retrieved - not a
   distribution.
3. Zero support is a 422 `no_support`, never a fabricated 0.5.

Two more facts, measured here:

4. The answer is always one of the offered options: the node renormalises
   across the question's labels.
5. The node retrieves neighbours by the **question text**, so the candidate
   names have to be the plain option names its history has labels for. A
   decorated name (`Button: 消息 (automation_id=...)`) retrieves nothing and
   comes back 422 - which is why the local judge asks for plain names
   (`JevScorer.candidate_key_style`).

Authentication: `Authorization: Bearer <key>` works. The node checks the key
value, not the scheme, so the hosted bearer header needs no second variant.

## The labelled history

| file | what it is |
|---|---|
| `openeyes/decision/history_seed.json` | 84 labelled examples kept in-repo (21 actions x 2 phrasings x 2 languages); seeding defaults to the Chinese half |
| `openeyes/decision/history_probe.json` | 42 held-out goals: the same options, phrased differently |
| `openeyes/decision/history_seed_wide.json` | 122-document Chinese-only experiment that widens options with product UI verbs |
| `openeyes/decision/history_probe_wide.json` | matching held-out set for the wide experiment |

Three things about the history are load-bearing:

- **Goal and option share a language.** The first version paired an English
  goal with a Chinese option; it matched neither a Chinese nor an English
  question except through the option token, and a goal only retrieves the
  documents of its own language.
- **The distractors stay in the document text** (`goal | label | distractors`).
  Removing them made wrong answers look high-confidence.
- **One family at a time.** The node holds a single decisions index; a mixed
  index lets one language's neighbours compete with the other's. The Chinese
  family is the measured default; English is opt-in until its wording is fixed.

```powershell
# seed the node (it stops the node, writes the index and starts it again:
# a node holds a lock on its data dir, and a killed node leaves the lock behind)
python examples\seed-systemone-history.py --index judgements --recreate            # Chinese only (default)
python examples\seed-systemone-history.py --index judgements --recreate --lang en  # English only
python examples\seed-systemone-history.py --index judgements --recreate --lang all # mixed, for comparison

# measure
$env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
$env:XERJ_API_KEY = (Get-Content E:\AI-Portable\xerj-data\admin.key -Raw).Trim()
python examples\systemone-calibration.py
```

## The measurement (2026-10-26, xerj v1.0.0-rc.99, k=10)

### Default: Chinese-only history (42 documents)

| set | goals | answered | right | accuracy |
|---|---|---|---|---|
| seed (seen, zh) | 42 | 42 | ~42 | ~1.00 |
| probe (held out, zh) | 21 | ~18-20 | ~15-18 | ~0.83-0.94 |

Exact answered counts move by one or two between runs on the same index.

### What separates, and what does not

Three candidate signals were measured on held-out goals:

- the **support share** (the winner's share of the vote weight);
- the **margin** (winner minus runner-up);
- the **engine score** of the neighbour carrying the winning label, from the
  node's `/_decide` evidence.

The margin and the engine score do **not** separate right from wrong: their
correct and wrong values interleave completely. The share separates only at
its top. Why the share is weak like that: the node normalises the neighbour
weights, so the top neighbour always carries weight 1.0 and the share
collapses onto a handful of values (0.3414, 0.1707, 0.0854 ...). It measures
*how relatively strong* the match is, not *whether it is right*.

### Wide-set control (do not ship as default)

Widening the Chinese option set with product UI verbs (审核 / 通过 / 发布 ...)
took accuracy from ~0.94 down to ~0.58 and wiped the act floor. The wide files
stay in-repo for reproduction (`--file openeyes/decision/history_seed_wide.json`)
and are not the default seed.

### English family (opt-in)

On an English-only index the held-out half answered roughly 14 of 21 goals
with about half of them right. Below the top the band is close to a coin flip,
so English stays opt-in until the goal phrasings share fewer common words.

## The thresholds (`openeyes/decision/scale.py`)

The thresholds follow the **scale** of the answer (`JevScore.scale`) and,
for the local judge, the **family** the candidates belong to
(`family_of`). Numbers below are the **conservative** end across run
variance: the act floor takes the highest value any run put above a wrong
answer; the review floor is the lowest share that still keeps the band at
**0.80** precision (a review suggestion is confirmed by a human).

| family | act (>=) | review (>=) | measured on the held-out goals |
|---|---|---|---|
| zh | 0.5121 | 0.2845 | 15 of 18 right; act 0 wrong; review ~0.83 |
| en | 0.4552 | 0.3414 | 7 of 14 right; act 0 wrong; review 0.80 |

Without candidates the measured (Chinese) family is used. The uncertainty
band is empty on purpose - a share has no "risky middle"; the gap between
the winner and the runner-up would be the signal, and the threshold model
has no place for it yet.

Two consequences worth stating plainly:

- The local judge is a **triage aid**, not an autopilot. On Chinese
  candidates it proposes strong matches for act and weaker ones for review;
  below the review floor it must not click on its own.
- The hosted thresholds do not transfer. On the hosted scale 0.34 is a
  fallback; on the share scale it can be a review. That is why the thresholds
  follow the scale and not the scorer.

## What is not solved

- **Twenty-one goals per family is a thin sample**, and the node's answers
  move by one or two between runs. Every threshold here is provisional;
  re-run the calibration and re-derive them when the history grows.
- **The English family is not ready.** It needs goal phrasings that do not
  share so many common words, and a bigger share of the history.
- **Sibling options hurt accuracy** when they are semantically close product
  verbs - keep the default set apart, use the wide files only to reproduce.
- The margin and the engine score are not usable as confidence - they are
  only diagnostics today (`POST /_decide` with the index and the question
  shows the neighbours, their labels, scores and weights).
- XERJ's own calibration (isotonic / temperature) fits the *positive label*
  of a binary history. A multi-option history is outside that fit, which is
  why the thresholds are measured here instead.
- ARPO (`simular-ai/ARPO`) is training code only; there is no ready labelled
  set to ingest. Expanding judgements has to be home-grown from OpenEyes
  Record replays.