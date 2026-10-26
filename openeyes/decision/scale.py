"""Confidence scales, and the thresholds that belong to each.

The hosted judge answers with a probability. A local XERJ history vote
answers with a **support share**: the winner's share of the vote weight
among the labelled neighbours the node retrieved. The two numbers are not
the same quantity, so the hosted thresholds (0.85 / 0.60) cannot be applied
to a share - a decisive local match measures around 0.34, a weak one 0.03.

Every scale therefore carries its own profile, and the score says which
scale it is on. The local profile is measured rather than guessed:
`examples/systemone-calibration.py` runs the repo's labelled history and a
held-out set of rephrased goals through a local node. On 20 held-out goals
the vote answered 17, of which 14 were right - and the share did **not**
separate right from wrong (a wrong answer reached the same 0.341 as eight
right ones). So the local judge is a triage aid, not an autopilot:

    share >= 0.90   ACT          1 of 14 right, 0 of 3 wrong
    share >= 0.25   review       8 of 14 right, 1 of 3 wrong
    share <  0.25   fallback     5 of 14 right, 2 of 3 wrong

See `docs/jev-local-judge.md` for the run and its raw numbers.
"""
from __future__ import annotations

from openeyes.decision.threshold import ThresholdConfig

SCALE_PROBABILITY = "probability"
SCALE_SUPPORT_SHARE = "support_share"

# Measured on the held-out goals (see docs/jev-local-judge.md). The
# uncertainty band is empty on purpose: a share has no "risky middle" - the
# gap between the winner and the runner-up would be the signal, and the
# threshold model has no place for it yet.
HISTORY_VOTE_THRESHOLDS = ThresholdConfig(
    confidence_floor=0.0,
    low_stakes_threshold=0.25,
    high_stakes_threshold=0.90,
    uncertain_low=0.0,
    uncertain_high=0.0,
)


def thresholds_for_scale(scale: str, cfg: ThresholdConfig | None = None) -> ThresholdConfig:
    """The thresholds that belong to a confidence scale.

    An explicitly supplied config always wins; otherwise the scale picks its
    own profile.
    """
    if cfg is not None:
        return cfg
    if scale == SCALE_SUPPORT_SHARE:
        return HISTORY_VOTE_THRESHOLDS
    return ThresholdConfig()
