r"""Acceptance probe for the JEV decision layer over a local XERJ node.

    $env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
    $env:TYPESAFE_API_KEY = (Get-Content D:\AI-Portable\xerj-data\admin.key -Raw).Trim()
    python examples\systemone-local-acceptance.py

Prints one JSON line and exits 0 when the decision layer answered through the
local node, 3 when it did not (same convention as the other acceptance
scripts in this folder).
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from openeyes.decision.scorer import JevAskError, TypesafeJevScorer  # noqa: E402

BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "http://127.0.0.1:9201")
KEY = (os.environ.get("TYPESAFE_API_KEY") or os.environ.get("XERJ_API_KEY") or "").strip()
GOAL = os.environ.get("SYSTEMONE_GOAL", "click the message button")
CANDIDATES = ["\u6d88\u606f", "\u6587\u4ef6"]


def main() -> int:
    scorer = TypesafeJevScorer(KEY, base_url=BASE_URL)
    try:
        score = scorer.ask(goal=GOAL, candidates=CANDIDATES, app_name="openeyes")
    except JevAskError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 3

    print(json.dumps({
        "passed": True,
        "transport": "http-systemone",
        "base_url": BASE_URL,
        "authenticated": bool(KEY),
        "goal": GOAL,
        "candidates": CANDIDATES,
        "target_index": score.target_index,
        "prob": score.prob,
        "confidence": score.confidence,
        "probabilities": sorted([list(item) for item in score.probabilities]),
        "raw": score.raw,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
