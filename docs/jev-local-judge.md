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
   distribution. Measured here: 0.341417 for a decisive match, 0.034 for a
   weak one.
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
| `openeyes/decision/history_seed.json` | 20 labelled examples (id, option, goal, distractors); the node's document is `{label, text}` with `text = "<goal> | <option> | <distractors>"` |
| `openeyes/decision/history_probe.json` | 20 held-out goals: the same options, phrased differently |

The seed alone can only show that the mechanism works - the document it
matches is the one the seed was built from. The probe is where the judge has
to generalise, so the probe is what the thresholds are measured on.

```powershell
# seed the node (it stops the node, writes the index, starts it again:
# a node holds a lock on its data dir, and a killed node leaves the lock behind)
python examples\seed-systemone-history.py --index judgements --recreate

# measure
$env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
$env:XERJ_API_KEY = (Get-Content E:\AI-Portable\xerj-data\admin.key -Raw).Trim()
python examples\systemone-calibration.py
```

## The measurement (2026-10-26, xerj v1.0.0-rc.99, k=10)

| set | goals | answered | right | accuracy |
|---|---|---|---|---|
| seed (seen) | 20 | 20 | 20 | 1.00 |
| probe (held out) | 20 | 17 | 14 | 0.82 |

Wrong answers: `open the chat inbox` -> 通知 (0.341), `change the preferences`
-> 刷新 (0.171), `duplicate the selection` -> 粘贴 (0.043).
No support (422): `keep my changes`, `produce a paper copy`, `mark every row`.

Shares of the right answers: 0.0569, 0.0854, 0.0854, 0.1707, 0.24, 0.3414 (x8), 1.0.
Shares of the wrong answers: 0.0427, 0.1707, 0.3414.

**The share does not separate right from wrong.** A wrong answer reached the
same 0.341 as eight right ones, and two right answers sat below every wrong
one. Any threshold that admits the common case also admits a wrong one.

## The thresholds (`openeyes/decision/scale.py`)

| band | threshold | held-out outcome |
|---|---|---|
| ACT | share >= 0.90 | 1 of 14 right, 0 of 3 wrong |
| review | 0.25 <= share < 0.90 | 8 of 14 right, 1 of 3 wrong |
| fallback | share < 0.25 | 5 of 14 right, 2 of 3 wrong |

The uncertainty band is empty on purpose: a share has no "risky middle" - the
gap between the winner and the runner-up would be the signal, and the
threshold model has no place for it yet.

Two consequences worth stating plainly:

- The local judge is a **triage aid**, not an autopilot. It proposes the
  right option on 82% of unseen phrasings, but below 0.90 it must not click
  on its own.
- The hosted thresholds do not transfer. On the hosted scale 0.34 is a
  fallback; on the share scale it is a review. That is why the thresholds
  follow the scale (`JevScore.scale`, `thresholds_for_scale`) and not the
  scorer.

## What is not solved

- Twenty examples is a small history. A goal outside it returns 422, and the
  layer then produces no step rather than a guess.
- Generalisation only reaches the option names the history knows. That fits a
  Chinese enterprise UI (保存 / 提交 / 取消 / 查询); it does not fit arbitrary
  element names.
- The margin between the winner and the runner-up is not modelled yet, and on
  this data it looks like the better signal than the share.
- XERJ's own calibration (isotonic / temperature) fits the *positive label*
  of a binary history. A multi-option history is outside that fit, which is
  why the thresholds are measured here instead.
