"""Act, then prove the act worked.

A delivered click is not an effective click. Any VPN's keepalive button
carries a live countdown ("0:18 left - click to reset"), so "the label
changed" proves nothing — only the countdown jumping back up proves the
session was renewed. The old keepalive loop compared labels, so every
missed click still reported success.

:func:`act_until` encodes that loop once, so every automation can reuse it:
act, wait for the expected state, retry (optionally with a different
strategy) when it did not take, and report what actually happened.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

Act = Callable[[], Any]
Expect = Callable[[], bool]


def wait_until(condition: Expect, *, timeout: float = 5.0,
               interval: float = 0.2) -> bool:
    """Poll ``condition`` until it holds or ``timeout`` expires.

    A raising condition counts as "not yet" — a transient UI error must not
    abort the wait.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            if condition():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.0, interval))


def act_until(act: Act, expect: Expect, *, retries: int = 3,
              timeout: float = 5.0, interval: float = 0.2,
              on_retry: Callable[[int, Any], None] | None = None) -> dict:
    """Run ``act`` until ``expect`` holds, then report what happened.

    ``act`` is called once per attempt; the caller can vary its strategy
    between attempts (e.g. UIA Invoke first, then a pixel click) by keeping
    state in a closure. ``expect`` is polled after every attempt.

    Returns ``{"ok", "attempts", "result", "elapsed", "error"}`` — ``ok`` is
    True only when the expected state was observed, never merely because
    the action was delivered.
    """
    attempts = 0
    started = time.monotonic()
    result: Any = None
    error: str | None = None
    max_attempts = max(1, int(retries))
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        try:
            result = act()
            error = None
        except Exception as e:
            result = None
            error = f"{type(e).__name__}: {e}"
        if wait_until(expect, timeout=timeout, interval=interval):
            return {"ok": True, "attempts": attempts, "result": result,
                    "elapsed": time.monotonic() - started, "error": error}
        if attempt < max_attempts and on_retry is not None:
            # only when another attempt follows: the hook may switch strategy
            on_retry(attempt, result)
    return {"ok": False, "attempts": attempts, "result": result,
            "elapsed": time.monotonic() - started, "error": error}
