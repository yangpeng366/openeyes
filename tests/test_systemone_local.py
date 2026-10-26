r"""Live probe: the JEV decision layer against a local XERJ node.

Skipped unless a local System One endpoint answers:

    $env:TYPESAFE_BASE_URL = "http://127.0.0.1:9201"
    $env:TYPESAFE_API_KEY = (Get-Content E:\AI-Portable\xerj-data\admin.key -Raw).Trim()
    pytest tests/test_systemone_local.py -v

A local XERJ node answers `/v1/systemone` with a rank-weighted vote over its
labelled history, so the contract under test is the *support score* one (the
winner's share of the labelled neighbours, losers at 0.0) rather than a
probability distribution.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

from openeyes.decision.scorer import JevAskError, TypesafeJevScorer

BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "http://127.0.0.1:9201")
# A local node keeps the API key on by default; it is only optional when the
# node was started with --insecure.
KEY = (os.environ.get("TYPESAFE_API_KEY") or os.environ.get("XERJ_API_KEY") or "").strip()


def _node_reachable() -> bool:
    try:
        urllib.request.urlopen(f"{BASE_URL}/v1/systemone", data=b"{}", timeout=3)
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False
    return True


def _scorer() -> TypesafeJevScorer:
    return TypesafeJevScorer(KEY, base_url=BASE_URL)


@pytest.mark.skipif(not _node_reachable(), reason=f"no local XERJ node on {BASE_URL}")
@pytest.mark.skipif(not KEY, reason="no TYPESAFE_API_KEY / XERJ_API_KEY for the local node")
def test_local_node_answers_the_system_one_wire():
    score = _scorer().ask(
        goal="click the message button",
        candidates=["\u6d88\u606f", "\u6587\u4ef6"],
        app_name="openeyes",
    )
    assert 0 <= score.target_index < 2
    assert 0.0 <= score.prob <= 1.0
    assert score.raw.get("type") == "choice"


@pytest.mark.skipif(not _node_reachable(), reason=f"no local XERJ node on {BASE_URL}")
@pytest.mark.skipif(not KEY, reason="no TYPESAFE_API_KEY / XERJ_API_KEY for the local node")
def test_local_node_reports_zero_support_as_an_error():
    with pytest.raises(JevAskError) as excinfo:
        _scorer().ask(
            goal="buy a ticket to mars for the rover",
            candidates=["\u8d2d\u4e70", "\u9000\u8d27"],
            app_name="openeyes",
        )
    assert "422" in str(excinfo.value)


@pytest.mark.skipif(not _node_reachable(), reason=f"no local XERJ node on {BASE_URL}")
@pytest.mark.skipif(not KEY, reason="no TYPESAFE_API_KEY / XERJ_API_KEY for the local node")
def test_the_node_answers_the_seed_and_never_invents_an_option():
    """Every answer must be one of the options that were offered."""
    from openeyes.decision.history import load_seed

    scorer = _scorer()
    for example in load_seed():
        try:
            score = scorer.ask(
                goal=example.goal,
                candidates=list(example.candidates),
                app_name="openeyes",
            )
        except JevAskError:
            continue  # a name the history has never seen is allowed to fail
        assert score.raw.get("choice") in example.candidates
        assert score.scale == "support_share"


if __name__ == "__main__":
    print(json.dumps({
        "base_url": BASE_URL,
        "reachable": _node_reachable(),
        "has_key": bool(KEY),
    }, ensure_ascii=False))
