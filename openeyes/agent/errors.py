"""Structured error codes, so a caller can route on them.

A sentence a human reads is not a contract: it changes with the author.
These codes do not. An agent decides what to do next - queue, degrade,
ask a human - by reading the code, not by matching on prose.

They are plain strings so that they survive JSON, MCP responses and
CLI output without any wrapper.
"""
from __future__ import annotations

#: The semantic path could not reach the control, and nothing may take the
#: foreground on the user's behalf.
BACKGROUND_UNAVAILABLE = "BACKGROUND_UNAVAILABLE"

#: The disturbance budget for this minute is used up.
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

#: The physical input lease was not granted within its deadline.
LEASE_TIMEOUT = "LEASE_TIMEOUT"

#: Another agent is already acting on this target.
TARGET_CONFLICT = "TARGET_CONFLICT"

#: The control offers no operation that can be delivered without the
#: pointer.
NO_SEMANTIC_PATH = "NO_SEMANTIC_PATH"

#: Every code this package can raise, for callers that want to validate.
ALL_CODES = (
    BACKGROUND_UNAVAILABLE,
    BUDGET_EXCEEDED,
    LEASE_TIMEOUT,
    TARGET_CONFLICT,
    NO_SEMANTIC_PATH,
)
