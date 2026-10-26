"""Confidence 3-segment threshold (parity with ACH FEAT-03 §1.1a).

Reference: docs/JEV_CONTEXT_SCORING_PLAN.md §1.1a
- confidence >= highStakesThreshold (0.85)  -> ACT (verbatim keep)
- lowStakesThreshold <= confidence < highStakesThreshold (0.60~0.85) -> FLAG_FOR_REVIEW
- confidence < lowStakesThreshold (0.60)  -> FALLBACK

Plus the `uncertain band` [0.30, 0.70] from consistency_noul cookbook:
prob in [0.30, 0.70] AND confidence < 0.85 -> UNCERTAIN (must verify).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DecisionBand(str, Enum):
    ACT = "act"
    FLAG_FOR_REVIEW = "flag_for_review"
    FALLBACK = "fallback"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ThresholdConfig:
    """Aligned with ACH `JevContextScorerConfig` FEAT-03 §1.1a."""
    confidence_floor: float = 0.50
    low_stakes_threshold: float = 0.60
    high_stakes_threshold: float = 0.85
    uncertain_low: float = 0.30
    uncertain_high: float = 0.70

    def __post_init__(self):
        for name in ("confidence_floor", "low_stakes_threshold", "high_stakes_threshold",
                     "uncertain_low", "uncertain_high"):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} out of [0,1]: {v}")
        if not (self.uncertain_low <= self.uncertain_high):
            raise ValueError("uncertain_low must be <= uncertain_high")
        if not (self.low_stakes_threshold <= self.high_stakes_threshold):
            raise ValueError("low_stakes_threshold must be <= high_stakes_threshold")


def classify_confidence(confidence: float, prob: float, cfg: ThresholdConfig) -> DecisionBand:
    """Map (confidence, prob) to one of four bands.

    Order matters: UNCERTAIN short-circuits the 3-segment when the
    prob is in the risky band but confidence is not yet high.
    """
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence out of [0,1]: {confidence}")
    if not (0.0 <= prob <= 1.0):
        raise ValueError(f"prob out of [0,1]: {prob}")

    # Uncertain band: prob in [uncertain_low, uncertain_high] AND confidence < high_stakes
    if cfg.uncertain_low <= prob <= cfg.uncertain_high and confidence < cfg.high_stakes_threshold:
        return DecisionBand.UNCERTAIN

    if confidence >= cfg.high_stakes_threshold:
        return DecisionBand.ACT
    if confidence >= cfg.low_stakes_threshold:
        return DecisionBand.FLAG_FOR_REVIEW
    return DecisionBand.FALLBACK