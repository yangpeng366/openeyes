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

The history is bilingual - the node holds one index, and a goal only
retrieves the documents of its own language, so the Chinese and the English
family are measured apart and get a profile each. On the held-out goals the
Chinese family separates (precision 0.75 at the bottom, 1.00 above 0.4552)
while the English one does not (0.45 overall, and roughly a coin flip
below the top) - so the English profile acts only on a very strong match.

    family   act (>=)   review (>=)   measured on the held-out goals
    zh       0.4552     0.2276        7 of 15 right act, 0 wrong; review 0.87
    en       0.4268     0.4268        2 of 5 right act, 0 wrong; no review band

See `docs/jev-local-judge.md` for the run, its raw numbers and what is still
unproven.
"""
from __future__ import annotations

from openeyes.decision.threshold import ThresholdConfig

SCALE_PROBABILITY = "probability"
SCALE_SUPPORT_SHARE = "support_share"

# Measured on the held-out goals (2026-10-26, 84-document history). The
# uncertainty band is empty on purpose: a share has no "risky middle" - the
# gap between the winner and the runner-up would be the signal, and the
# threshold model has no place for it yet.
HISTORY_VOTE_THRESHOLDS_ZH = ThresholdConfig(
    confidence_floor=0.0,
    low_stakes_threshold=0.2276,
    high_stakes_threshold=0.4552,
    uncertain_low=0.0,
    uncertain_high=0.0,
)

# The English family is not proven yet: 11 of 21 held-out goals were answered
# at all and the ones below the top are close to a coin flip. So it acts only
# on a strong match and offers nothing for review.
HISTORY_VOTE_THRESHOLDS_EN = ThresholdConfig(
    confidence_floor=0.0,
    low_stakes_threshold=0.4268,
    high_stakes_threshold=0.4268,
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
    to. Without candidates the stricter of the two is used.
    """
    if cfg is not None:
        return cfg
    if scale == SCALE_SUPPORT_SHARE:
        if candidates is None:
            return HISTORY_VOTE_THRESHOLDS_ZH
        if family_of(candidates) == "zh":
            return HISTORY_VOTE_THRESHOLDS_ZH
        return HISTORY_VOTE_THRESHOLDS_EN
    return ThresholdConfig()
