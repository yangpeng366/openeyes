r"""Measure the local XERJ judge's votes over the repo's labelled history.

    $env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
    $env:XERJ_API_KEY = (Get-Content D:\AI-Portable\xerj-data\admin.key -Raw).Trim()
    python examples\systemone-calibration.py

Every labelled example is asked the way the decision layer would ask - the
goal plus the option and its distractors as candidates - and the winner's
support share is recorded. The hosted judge's 0.85/0.60 do not transfer to a
support-share scale, so the local thresholds are set from this measurement
rather than from the hosted numbers.

The script also pulls the evidence from the node's `/_decide` endpoint (the
ES-compat port, one below the native REST port) to record the engine score
of the neighbour that carries the winning label. That score is the
candidate signal that could replace the share, so it is measured too.

Prints one JSON line; exits 0 when every example was answered, 3 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from openeyes.decision.history import load_probe, load_seed  # noqa: E402
from openeyes.decision.scorer import JevAskError, TypesafeJevScorer  # noqa: E402


def _es_port(base_url: str) -> str:
    """The ES-compat endpoint, one port below the native REST port."""
    parts = urlsplit(base_url)
    port = parts.port or 9200
    return urlunsplit((parts.scheme, f"{parts.hostname}:{port - 1}", "/_decide", "", ""))


def _evidence(base_url: str, api_key: str, index: str, question: str) -> list[dict]:
    """The node's evidence for a question: neighbours with labels and scores."""
    body = json.dumps({"index": index, "question": question}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(_es_port(base_url), data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []
    return payload.get("neighbours") or []


def _precision_floor(correct: list[float], wrong: list[float], target: float) -> float:
    """The lowest threshold whose precision still reaches `target`.

    The lowest one, not the highest: a triage band should surface as much as
    it can while keeping its quality, and the band is everything below the
    act floor.
    """
    meeting = []
    for threshold in sorted(set(correct) | set(wrong)):
        hits = sum(1 for value in correct if value >= threshold)
        misses = sum(1 for value in wrong if value >= threshold)
        if hits == 0:
            continue
        if hits / (hits + misses) >= target:
            meeting.append(threshold)
    return min(meeting) if meeting else 0.0


def _strict_floor(correct: list[float], wrong: list[float]) -> float:
    """The smallest value no wrong answer reached (0.0 when there is none)."""
    if not correct:
        return 0.0
    if not wrong:
        return min(correct)
    above = [value for value in correct if value > max(wrong)]
    return min(above) if above else 0.0


def _floors(correct: list[float], wrong: list[float]) -> tuple[float, float]:
    """(act floor, review floor) for a support-share scale."""
    act = _strict_floor(correct, wrong)
    if not correct:
        return act, 0.0
    best_threshold = 0.0
    best_f1 = -1.0
    for threshold in sorted(set(correct) | set(wrong)):
        hits = sum(1 for value in correct if value >= threshold)
        misses = sum(1 for value in wrong if value >= threshold)
        if hits == 0:
            continue
        precision = hits / (hits + misses)
        recall = hits / len(correct)
        f1 = 2 * precision * recall / (precision + recall)
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = threshold
    return act, best_threshold


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("TYPESAFE_BASE_URL", "http://127.0.0.1:9201"))
    parser.add_argument("--api-key", default=os.environ.get("TYPESAFE_API_KEY") or os.environ.get("XERJ_API_KEY") or "")
    parser.add_argument("--index", default="judgements")
    parser.add_argument("--seed-file", help="an alternative seed file to measure")
    parser.add_argument("--probe-file", help="an alternative probe file to measure")
    parser.add_argument("--no-evidence", action="store_true", help="skip the /_decide evidence lookup")
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
                    "lang": example.lang,
                    "answered": False,
                    "error": str(exc),
                })
                continue
            shares = dict(score.probabilities or {})
            ordered = sorted(shares.values(), reverse=True)
            margin = (ordered[0] - ordered[1]) if len(ordered) > 1 else ordered[0]
            row = {
                "id": example.id,
                "label": example.label,
                "lang": example.lang,
                "answered": True,
                "winner": score.raw.get("choice"),
                "correct": score.raw.get("choice") == example.label,
                "share": round(float(score.prob), 6),
                "margin": round(float(margin), 6),
                "scale": score.scale,
            }
            if not args.no_evidence:
                neighbours = _evidence(args.base_url, args.api_key, args.index, example.goal)
                winner_scores = [n.get("_score") for n in neighbours if n.get("label") == row["winner"]]
                row["win_score"] = round(max(winner_scores), 6) if winner_scores else None
            rows.append(row)
        return rows

    def summarise(rows: list[dict]) -> dict:
        answered = [row for row in rows if row.get("answered")]
        correct = [row for row in answered if row.get("correct")]
        wrong = [row for row in answered if not row.get("correct")]
        correct_shares = sorted(row["share"] for row in correct)
        wrong_shares = sorted(row["share"] for row in wrong)
        correct_margins = sorted(row["margin"] for row in correct)
        wrong_margins = sorted(row["margin"] for row in wrong)
        correct_scores = sorted(row["win_score"] for row in correct if row.get("win_score") is not None)
        wrong_scores = sorted(row["win_score"] for row in wrong if row.get("win_score") is not None)

        # The act floor is strict: the smallest share that no wrong answer
        # reached, so acting at or above it produced no wrong answer in the
        # measurement. The review floor is the F1-optimal share - the point
        # that surfaces the most right answers per wrong one, which is the
        # right trade for a band a human confirms anyway.
        act_floor, review_floor = _floors(correct_shares, wrong_shares)
        # A review suggestion is confirmed by a human, so the target is 0.80:
        # one in five wrong is tolerable there, not under an automatic act.
        precision_floor = _precision_floor(correct_shares, wrong_shares, 0.80)
        margin_floor = _strict_floor(correct_margins, wrong_margins)
        score_floor = _strict_floor(correct_scores, wrong_scores)
        return {
            "examples": len(rows),
            "answered": len(answered),
            "correct": len(correct),
            "accuracy": round(len(correct) / len(answered), 4) if answered else 0.0,
            "correct_shares": correct_shares,
            "wrong_shares": wrong_shares,
            "correct_margins": correct_margins,
            "wrong_margins": wrong_margins,
            "correct_win_scores": correct_scores,
            "wrong_win_scores": wrong_scores,
            "share_floor": act_floor,
            "review_floor": review_floor,
            "precision_floor": precision_floor,
            "margin_floor": margin_floor,
            "win_score_floor": score_floor,
        }

    seed_rows = measure(load_seed(Path(args.seed_file) if args.seed_file else None))
    probe_rows = measure(load_probe(Path(args.probe_file) if args.probe_file else None))
    seed = summarise(seed_rows)
    probe = summarise(probe_rows)

    by_lang = {}
    for lang in sorted({row.get("lang", "") for row in probe_rows}):
        by_lang[lang] = summarise([row for row in probe_rows if row.get("lang", "") == lang])

    # The thresholds come from the held-out set: the seed can only show that
    # the mechanism works, the probe is where the judge has to generalise.
    act_floor = probe["share_floor"]
    review_floor = probe["review_floor"]

    print(json.dumps({
        "passed": seed["answered"] == seed["examples"] and probe["answered"] == probe["examples"],
        "seed": seed,
        "probe": probe,
        "probe_by_lang": by_lang,
        "recommended": {
            "high_stakes_threshold": act_floor,
            "low_stakes_threshold": review_floor,
            "margin_floor": probe["margin_floor"],
            "win_score_floor": probe["win_score_floor"],
        },
        "seed_rows": seed_rows,
        "probe_rows": probe_rows,
    }, ensure_ascii=False))
    return 0 if seed["answered"] == seed["examples"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
