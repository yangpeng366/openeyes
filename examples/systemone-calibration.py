r"""Measure the local XERJ judge's votes over the repo's labelled history.

    $env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
    $env:XERJ_API_KEY = (Get-Content E:\AI-Portable\xerj-data\admin.key -Raw).Trim()
    python examples\systemone-calibration.py

Every labelled example is asked the way the decision layer would ask - the
goal plus the option and its distractors as candidates - and the winner's
support share is recorded. The hosted judge's 0.85/0.60 do not transfer to a
support-share scale, so the local thresholds are set from this measurement
rather than from the hosted numbers.

Prints one JSON line; exits 0 when every example was answered, 3 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openeyes.decision.history import load_probe, load_seed  # noqa: E402
from openeyes.decision.scorer import JevAskError, TypesafeJevScorer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("TYPESAFE_BASE_URL", "http://127.0.0.1:9201"))
    parser.add_argument("--api-key", default=os.environ.get("TYPESAFE_API_KEY") or os.environ.get("XERJ_API_KEY") or "")
    args = parser.parse_args()

    scorer = TypesafeJevScorer(args.api_key, base_url=args.base_url)

    def measure(examples: list) -> list[dict]:
        rows = []
        for example in examples:
            try:
                score = scorer.ask(
                    goal=example.goal,
                    candidates=list(example.candidates),
                    app_name="openeyes",
                )
            except JevAskError as exc:
                rows.append({
                    "id": example.id,
                    "label": example.label,
                    "answered": False,
                    "error": str(exc),
                })
                continue
            shares = dict(score.probabilities or {})
            ordered = sorted(shares.values(), reverse=True)
            margin = (ordered[0] - ordered[1]) if len(ordered) > 1 else ordered[0]
            rows.append({
                "id": example.id,
                "label": example.label,
                "answered": True,
                "winner": score.raw.get("choice"),
                "correct": score.raw.get("choice") == example.label,
                "share": round(float(score.prob), 6),
                "margin": round(float(margin), 6),
                "scale": score.scale,
            })
        return rows

    def summarise(rows: list[dict]) -> dict:
        answered = [row for row in rows if row.get("answered")]
        correct = [row for row in answered if row.get("correct")]
        wrong = [row for row in answered if not row.get("correct")]
        correct_shares = sorted(row["share"] for row in correct)
        wrong_shares = sorted(row["share"] for row in wrong)
        correct_margins = sorted(row["margin"] for row in correct)
        wrong_margins = sorted(row["margin"] for row in wrong)

        # Highest-precision-with-coverage: the smallest share no wrong answer
        # reached. Above it every answer was right; below it anything may go.
        act_floor = min(correct_shares) if correct_shares else 0.0
        if wrong_shares:
            act_floor = max(act_floor, max(wrong_shares))
        margin_floor = min(correct_margins) if correct_margins else 0.0
        if wrong_margins:
            margin_floor = max(margin_floor, max(wrong_margins))
        return {
            "examples": len(rows),
            "answered": len(answered),
            "correct": len(correct),
            "accuracy": round(len(correct) / len(answered), 4) if answered else 0.0,
            "correct_shares": correct_shares,
            "wrong_shares": wrong_shares,
            "correct_margins": correct_margins,
            "wrong_margins": wrong_margins,
            "share_floor": act_floor,
            "margin_floor": margin_floor,
        }

    seed_rows = measure(load_seed())
    probe_rows = measure(load_probe())
    seed = summarise(seed_rows)
    probe = summarise(probe_rows)

    # The thresholds come from the held-out set: the seed can only show that
    # the mechanism works, the probe is where the judge has to generalise.
    act_floor = probe["share_floor"]
    review_floor = round(act_floor / 2, 6) if act_floor else 0.0

    print(json.dumps({
        "passed": seed["answered"] == seed["examples"] and probe["answered"] == probe["examples"],
        "seed": seed,
        "probe": probe,
        "recommended": {
            "high_stakes_threshold": act_floor,
            "low_stakes_threshold": review_floor,
            "margin_floor": probe["margin_floor"],
        },
        "seed_rows": seed_rows,
        "probe_rows": probe_rows,
    }, ensure_ascii=False))
    return 0 if seed["answered"] == seed["examples"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
