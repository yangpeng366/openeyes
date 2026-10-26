"""Confidence scales, and the thresholds that belong to each.

The hosted judge answers with a probability. A local XERJ history vote
answers with a **support share**: the winner's share of the vote weight
among the labelled neighbours the node retrieved. The two numbers are not
the same quantity, so the hosted thresholds (0.85 / 0.60) cannot be applied
to a share - a decisive local match measures around 0.34, a weak one 0.03.

Every scale therefore carries its own profile, and the score says which
scale it is on. The local profile is measured rather than guessed:
`examples/systemone-calibration.py` runs the repo's labelled history and a
held-out set of rephrased goals through a local node.

The history holds one family at a time - the node keeps a single decisions
index, and a mixed index lets one language's neighbours compete with the
other's. The Chinese family is the measured one and the default; the
English family is opt-in until its wording is fixed.

    family   act (>=)   review (>=)   measured on the held-out goals
    zh       0.5121     0.2845        15 of 18 right; act 0 wrong; review 0.83
    en       0.4552     0.3414        7 of 14 right; act 0 wrong; review 0.80

Both numbers are the conservative end of what the runs showed: the node
answers one or two goals differently from run to run, so the act floor
takes the highest value any run put above a wrong answer, and the review
floor the one that keeps the band at 0.80 precision.

See `docs/jev-local-judge.md` for the runs, their raw numbers and what is
still unproven.
"""
from __future__ import annotations

from openeyes.decision.threshold import ThresholdConfig

SCALE_PROBABILITY = "probability"
SCALE_SUPPORT_SHARE = "support_share"

# Measured on the held-out goals (2026-10-26, 42-document Chinese history).
# The uncertainty band is empty on purpose: a share has no "risky middle" -
# the gap between the winner and the runner-up would be the signal, and the
# threshold model has no place for it yet.
HISTORY_VOTE_THRESHOLDS_ZH = ThresholdConfig(
    confidence_floor=0.0,
    low_stakes_threshold=0.2845,
    high_stakes_threshold=0.5121,
    uncertain_low=0.0,
    uncertain_high=0.0,
)

# The English family is weaker: 14 of 21 held-out goals were answered at
# all and the ones below the top are close to a coin flip. It is opt-in
# (the seeding script seeds the Chinese family by default).
HISTORY_VOTE_THRESHOLDS_EN = ThresholdConfig(
    confidence_floor=0.0,
    low_stakes_threshold=0.3414,
    high_stakes_threshold=0.4552,
    uncertain_low=0.0,
    uncertain_high=0.0,
)


def family_of(labels) -> str:
    """Which family of the bilingual history a candidate set belongs to.

    The history is stored per language, so the candidates say which family
    is in play; a label with a Han character is the Chinese family.
    """
    for label in labels or ():
        for character in str(label):
            if "\u4e00" <= character <= "\u9fff":
                return "zh"
    return "en"


def thresholds_for_scale(scale: str, cfg: ThresholdConfig | None = None, candidates=None) -> ThresholdConfig:
    """The thresholds that belong to a confidence scale.

    An explicitly supplied config always wins; otherwise the scale picks its
    own profile, and the local profile is the family the candidates belong
    to. Without candidates the measured (Chinese) family is used.
    """
    if cfg is not None:
        return cfg
    if scale == SCALE_SUPPORT_SHARE:
        if candidates is not None and family_of(candidates) == "en":
            return HISTORY_VOTE_THRESHOLDS_EN
        return HISTORY_VOTE_THRESHOLDS_ZH
    return ThresholdConfig()
