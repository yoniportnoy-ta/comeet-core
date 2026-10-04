"""Comeet error taxonomy.

Three levels, because the caller does different things with each: a transient
5xx is worth retrying, a bandwidth 429 means back off hard, and everything else
is the caller's problem to handle.
"""
from __future__ import annotations

class ComeetError(RuntimeError):
    """Non-2xx response from api.comeet.co. Carries status + body for debugging."""

    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class ComeetBandwidthError(ComeetError):
    """429 / 'Bandwidth quota exceeded'. Caller should back off."""


class ComeetTransientError(ComeetError):
    """5xx / 302 splash / network blip. Caller should retry."""


def _is_transient_status(code: int) -> bool:
    return code in (302, 502, 503, 504)


def _is_bandwidth_response(code: int, body_text: str) -> bool:
    if code == 429:
        return True
    lower = (body_text or "").lower()
    return "bandwidth quota" in lower or "rate of data transfer" in lower


