"""OpenEyes optional decision layer (Jev / System One).

Backend-agnostic: consumes a list of `openeyes.Element` (any backend that
produces Element) and produces a single `Action` per step.

Default is **disabled** (`enabled=False`). When disabled, callers should
bypass the decision layer entirely and continue to use existing scripted
flows (e.g. `eyes` CLI verbs / `click_by_selector`).

When enabled, the layer optionally calls a Jev scorer
(`https://api.typesafe.ai/v1/systemone`) to pick among candidates.
The 3-segment confidence thresholds reuse the same constants as
ACH FEAT-03 §1.1a (`docs/JEV_CONTEXT_SCORING_PLAN.md` §1.1a) to keep
routing decisions consistent across projects.

Why a thin layer (not a fork of JevClient):
- The TypeSafe Jev protocol is already implemented in
  `agent-cloud-harness` (`com.agentcloud.scorer.*`) with comprehensive
  contract tests. Re-implementing it in Python risks drift.
- This layer is `metadata Map<String,Object>`-style **Contract-Additive**
  on top of `Element[]`; it does not replace `Element` / `Action`
  semantics, it just routes between fake Jev and real Jev.
"""
from __future__ import annotations
from openeyes.decision.threshold import ThresholdConfig, classify_confidence, DecisionBand
from openeyes.decision.action import Action, ActionKind
from openeyes.decision.scorer import JevScorer, FakeJevScorer, JevAskError, JevScore
from openeyes.decision.router import decide, DecisionContext

__all__ = [
    "ThresholdConfig", "classify_confidence", "DecisionBand",
    "Action", "ActionKind",
    "JevScorer", "FakeJevScorer", "JevAskError", "JevScore",
    "decide", "DecisionContext",
]