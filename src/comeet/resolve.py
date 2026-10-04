"""Turning whatever identifier you were handed into the one the API accepts.

Comeet shows a NUMERIC id in the recruiter UI and in profile URLs, but the
Recruiting API keys on an ALPHANUMERIC uid ("B4.B5F21") and answers a numeric
one with 404 "The provided ID could not be found" — which reads like "no such
candidate" and is why this module exists.

Resolution is deliberately layered so the expensive step runs last, and the
cache layer is injected: core does not own a database.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger(__name__)

_NUMERIC = re.compile(r"^\d+$")
_URL_ID = re.compile(r"/can/(\d+)")


def numeric_id(ident: str) -> Optional[str]:
    """The numeric id inside a raw id or a Comeet profile URL, if there is one."""
    s = (ident or "").strip()
    if _NUMERIC.match(s):
        return s
    m = _URL_ID.search(s)
    return m.group(1) if m else None


def is_api_uid(ident: str) -> bool:
    """True when this already looks like the alphanumeric uid the API wants."""
    return bool(re.match(r"^[0-9A-F]{2}\.[0-9A-F]{4,6}$", (ident or "").strip(), re.I))


def resolve_uid(
    ident: str,
    *,
    client: Any,
    lookup: Optional[Callable[[str], Optional[str]]] = None,
    positions: Optional[Iterable[str]] = None,
    scan_budget_s: float = 8.0,
) -> Optional[str]:
    """Return the alphanumeric uid, or None when it cannot be resolved.

    Order, cheapest first:
      1. already a uid            -> pass straight through
      2. `lookup(numeric_id)`     -> the caller's cache or mined corpus
      3. live scan of `positions` -> capped by `scan_budget_s`

    The budget matters: an unresolvable id once took 178 seconds of scanning
    before anyone noticed, on a request path a human was waiting on. Returning
    None promptly is better than resolving eventually.
    """
    s = (ident or "").strip()
    if not s:
        return None
    if is_api_uid(s):
        return s
    num = numeric_id(s)
    if num is None:
        return s  # not numeric and not a uid — let the caller try it as-is

    if lookup is not None:
        try:
            hit = lookup(num)
        except Exception as exc:  # noqa: BLE001 — a cold cache is not fatal
            log.info("uid lookup failed for %s: %s", num, exc)
            hit = None
        if hit:
            return hit

    if not positions:
        return None
    deadline = time.monotonic() + scan_budget_s
    for puid in positions:
        if time.monotonic() > deadline:
            log.info("uid scan budget exhausted before resolving %s", num)
            return None
        try:
            for c in client.list_candidates_for_position(puid) or []:
                url = c.get("URL") or ""
                if numeric_id(url) == num:
                    return str(c.get("uid") or "") or None
        except Exception as exc:  # noqa: BLE001
            log.info("uid scan: position %s failed: %s", puid, exc)
            continue
    return None
