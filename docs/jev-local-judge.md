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
| `openeyes/decision/history_seed.json` | 84 labelled examples: 21 actions x 2 phrasings x 2 languages |
| `openeyes/decision/history_probe.json` | 42 held-out goals: the same options, phrased differently |

Two things about the history are load-bearing:

- **Goal and option share a language.** The first version paired an English
  goal with a Chinese option; it matched neither a Chinese nor an English
  question except through the option token, and a goal only retrieves the
  documents of its own language. A Chinese goal retrieves the Chinese
  documents only - an index seeded with the other language answers 0 of 21
  goals.
- **The distractors are options of the same family**, for the same reason.

```powershell
# seed the node (it stops the node, writes the index and starts it again:
# a node holds a lock on its data dir, and a killed node leaves the lock behind)
python examples\seed-systemone-history.py --index judgements --recreate
python examples\seed-systemone-history.py --index judgements --recreate --lang zh

# measure
$env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
$env:XERJ_API_KEY = (Get-Content E:\AI-Portable\xerj-data\admin.key -Raw).Trim()
python examples\systemone-calibration.py
```

## The measurement (2026-10-26, xerj v1.0.0-rc.99, k=10, 84 documents)

| set | goals | answered | right | accuracy |
|---|---|---|---|---|
| seed (seen) | 84 | 84 | 84 | 1.00 |
| probe (held out) | 42 | 31 | 20 | 0.65 |
| probe, Chinese family | 21 | 20 | 15 | 0.75 |
| probe, English family | 21 | 11 | 5 | 0.45 |

### What separates, and what does not

Three candidate signals were measured on the held-out goals:

- the **support share** (the winner's share of the vote weight);
- the **margin** (winner minus runner-up);
- the **engine score** of the neighbour carrying the winning label, from the
  node's `/_decide` evidence.

The margin and the engine score do **not** separate right from wrong: their
correct and wrong values interleave completely (a wrong answer reached an
engine score of 8.13 while right answers sat at 1.10). The share separates
only at its top: above ~0.43 every measured answer was right, while the
middle of the range is a coin flip (a band precision of about 0.54).

Why the share is weak like that: the node normalises the neighbour weights,
so the top neighbour always carries weight 1.0 and the share collapses onto
a handful of values (0.3414, 0.1707, 0.0854 ...). It measures *how
relatively strong* the match is, not *whether it is right*.

### Per family

| family | share | right | wrong | precision |
|---|---|---|---|---|
| zh | >= 0.4552 | 7 | 0 | 1.00 |
| zh | >= 0.2276 | 13 | 2 | 0.87 |
| zh | >= 0.0721 | 15 | 5 | 0.75 |
| en | >= 0.4268 | 2 | 0 | 1.00 |
| en | >= 0.4097 | 2 | 1 | 0.67 |
| en | >= 0.0806 | 5 | 6 | 0.45 |

The Chinese family separates - its precision rises with the share. The
English family does not: below the top it is close to a coin flip, and only
11 of 21 goals were answered at all (its goal phrasings share common words,
so the retrieval is noisy).

## The thresholds (`openeyes/decision/scale.py`)

The thresholds follow the **scale** of the answer (`JevScore.scale`) and,
for the local judge, the **family** the candidates belong to
(`family_of`):

| family | act (>=) | review (>=) | measured |
|---|---|---|---|
| zh | 0.4552 | 0.2276 | 7 of 15 right act with 0 wrong; the review band is 0.87 precise |
| en | 0.4268 | 0.4268 | 2 of 5 right act with 0 wrong; no review band - the English half is unproven |

The act floor is strict: the smallest share that no wrong answer reached.
The review floor is the lowest share whose precision still reaches 0.85 for
the family. The uncertainty band is empty on purpose - a share has no
"risky middle"; the gap between the winner and the runner-up would be the
signal, and the threshold model has no place for it yet.

Two consequences worth stating plainly:

- The local judge is a **triage aid**, not an autopilot. On Chinese
  candidates it proposes the right option on 75% of unseen phrasings and
  acts on the strong ones; below the act floor it must not click on its own.
- The hosted thresholds do not transfer. On the hosted scale 0.34 is a
  fallback; on the share scale it is a review. That is why the thresholds
  follow the scale and not the scorer.

## What is not solved

- **Twenty-one goals per family is a thin sample**, and the node's answers
  move by one or two between runs (the same index answered 31 and 33 of 42
  on two runs). Every threshold here is provisional; re-run the
  calibration and re-derive them when the history grows.
- **The English family is not ready.** It needs goal phrasings that do not
  share so many common words, and a bigger share of the history.
- **Precision and coverage trade off against the history size**: the first
  20-document history answered 17 of 21 goals with 0.82 accuracy, the
  84-document one answers 31 of 42 with 0.65. A small history is sharp but
  silent, a big one is talkative and less sure.
- The margin and the engine score are not usable as confidence - they are
  only diagnostics today (`POST /_decide` with the index and the question
  shows the neighbours, their labels, scores and weights).
- XERJ's own calibration (isotonic / temperature) fits the *positive label*
  of a binary history. A multi-option history is outside that fit, which is
  why the thresholds are measured here instead.
