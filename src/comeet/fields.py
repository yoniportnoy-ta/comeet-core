"""Reading Comeet's payloads.

Pure functions over the dicts the API returns. No policy lives here — anything
that asks "should we act on this candidate" belongs to the calling service,
because the answer differs per service.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

log = __import__("logging").getLogger(__name__)


def candidate_max_activity_iso(candidate: dict[str, Any]) -> datetime | None:
    """Latest of time_created / time_last_status_changed — used as the cursor.

    Equivalent of `maxCandidateActivityTime_` in Code.gs.
    """
    candidates = []
    for key in ("time_created", "time_last_status_changed"):
        raw = candidate.get(key)
        if not raw:
            continue
        try:
            # Comeet timestamps are ISO 8601 with trailing Z (UTC).
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            candidates.append(ts)
        except (ValueError, TypeError):
            pass
    return max(candidates) if candidates else None


def candidate_full_name(candidate: dict[str, Any]) -> str:
    parts = [
        (candidate.get("first_name") or "").strip(),
        (candidate.get("last_name") or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def position_country(position: dict[str, Any], *, unknown: str = "") -> str:
    """The position's country as a full name — "Israel", not "IL".

    Probes `location.country`, then a top-level `country` (string or dict,
    which some tenants use), then the last segment of the full location, and
    expands ISO codes at the end. `unknown` is what to return when nothing
    resolves: callers rendering for people pass "(unknown)", callers
    bucketing for filters leave it empty.

    The expansion is the point. pipey had its own copy that probed the raw
    `country` field first and returned the ISO code unexpanded, so 393 of 491
    positions rendered as "IL"/"CA"/"US" in recruiter-facing reports despite
    its docstring promising otherwise.
    """
    loc = position.get("location") or {}
    raw = (loc.get("country") or "").strip()

    if not raw:
        c = position.get("country")
        if isinstance(c, str):
            raw = c.strip()
        elif isinstance(c, dict):
            for k in ("name", "country", "code"):
                v = c.get(k)
                if isinstance(v, str) and v.strip():
                    raw = v.strip()
                    break

    if not raw:
        full = position_full_location(position)
        segments = [seg.strip() for seg in (full or "").split(",") if seg.strip()]
        raw = segments[-1] if segments else ""

    return _expand_country_display(raw) if raw else unknown


def position_full_location(position: dict[str, Any]) -> str:
    loc = position.get("location") or {}
    if loc.get("name"):
        return str(loc["name"])
    parts = [loc.get("city"), loc.get("state"), loc.get("country")]
    return ", ".join(p for p in parts if p)


def position_lead_recruiter(position: dict[str, Any]) -> str:
    users = (position.get("users") or {}).get("lead_recruiter") or {}
    parts = [
        (users.get("first_name") or "").strip(),
        (users.get("last_name") or "").strip(),
    ]
    return " ".join(p for p in parts if p)


_COUNTRY_EXPANSIONS = {
    # Israel — country codes and common cities
    "IL": "Israel",
    "ISR": "Israel",
    "ISRAEL": "Israel",
    "TELAVIV": "Israel",
    "TELAVIVYAFO": "Israel",
    "TLV": "Israel",
    "JERUSALEM": "Israel",
    "HAIFA": "Israel",
    "HERZLIYA": "Israel",
    "HERTZLIYA": "Israel",
    "RAMATGAN": "Israel",
    "BEERSHEVA": "Israel",
    "BEERSHEBA": "Israel",
    "REHOVOT": "Israel",
    "NETANYA": "Israel",
    "RAANANA": "Israel",
    "PETACHTIKVA": "Israel",
    "RISHONLEZION": "Israel",
    # United States
    "US": "United States",
    "USA": "United States",
    "UNITEDSTATES": "United States",
    "AMERICA": "United States",
    "NEWYORK": "United States",
    "NYC": "United States",
    "NEWYORKNY": "United States",
    "SANFRANCISCO": "United States",
    "SF": "United States",
    "LOSANGELES": "United States",
    "LA": "United States",
    "AUSTIN": "United States",
    "BOSTON": "United States",
    "CHICAGO": "United States",
    "SEATTLE": "United States",
    "MIAMI": "United States",
    "REMOTEUS": "United States",
    # Canada
    "CA": "Canada",       # note: this also matches California, but Comeet usually
    "CAN": "Canada",      #       writes "California" in full so we accept the risk
    "CANADA": "Canada",
    "TORONTO": "Canada",
    "MONTREAL": "Canada",
    "VANCOUVER": "Canada",
    "OTTAWA": "Canada",
    "CALGARY": "Canada",
    # United Kingdom
    "UK": "United Kingdom",
    "GB": "United Kingdom",
    "UNITEDKINGDOM": "United Kingdom",
    "LONDON": "United Kingdom",
    "MANCHESTER": "United Kingdom",
    # United Arab Emirates
    "UAE": "United Arab Emirates",
    "DUBAI": "United Arab Emirates",
    "ABUDHABI": "United Arab Emirates",
    # Germany
    "DE": "Germany",
    "DEU": "Germany",
    "GERMANY": "Germany",
    "BERLIN": "Germany",
    "MUNICH": "Germany",
    # France
    "FR": "France",
    "FRA": "France",
    "FRANCE": "France",
    "PARIS": "France",
    # India
    "IN": "India",
    "IND": "India",
    "INDIA": "India",
    "BANGALORE": "India",
    "BENGALURU": "India",
    "MUMBAI": "India",
    "HYDERABAD": "India",
    "PUNE": "India",
    "NEWDELHI": "India",
}


def _expand_country_display(raw: str) -> str:
    import re as _re
    s = (raw or "").strip()
    if not s:
        return ""
    # Strip everything but letters and uppercase, so "Tel-Aviv", "Tel Aviv",
    # "TEL.AVIV", "Tel_Aviv" all collapse to "TELAVIV".
    compact = _re.sub(r"[^A-Za-z]", "", s).upper()
    if compact in _COUNTRY_EXPANSIONS:
        return _COUNTRY_EXPANSIONS[compact]
    if len(s) == 2 and s.isalpha():
        try:
            from babel import Locale
            return Locale("en").territories.get(s.upper(), s)
        except ImportError:
            pass
    return s


_RECRUITER_NOTE_TOKENS = ("note", "internal", "recruiter", "comment", "memo", "extra")


def _looks_like_recruiter_note_block(name: str) -> bool:
    """Heuristic: does this details[] block hold recruiter-added notes
    rather than formal JD prose (description, requirements, etc.)?"""
    if not name:
        return False
    n = name.lower()
    return any(tok in n for tok in _RECRUITER_NOTE_TOKENS)


def _strip_html(value: str) -> str:
    import re
    # Strip HTML tags — same as Code.gs's `replace(/<[^>]+>/g, ' ')`.
    return re.sub(r"<[^>]+>", " ", value).strip()


def position_jd_text(position: dict[str, Any]) -> str:
    """Equivalent of `buildPositionJdText_` — prose JD passed to Claude.

    Returns only the "formal JD" pieces: name/department/location/level + any
    details[] block whose name doesn't look like a recruiter note. The recruiter
    notes are surfaced separately via `position_recruiter_notes()` so the prompt
    can weight them distinctly.
    """
    lines: list[str] = []
    lines.append(f"Position: {position.get('name') or ''}")
    if position.get("department"):
        lines.append(f"Department: {position['department']}")
    if (loc := (position.get("location") or {}).get("name")):
        lines.append(f"Location: {loc}")
    if position.get("experience_level"):
        lines.append(f"Experience level: {position['experience_level']}")
    if position.get("employment_type"):
        lines.append(f"Employment: {position['employment_type']}")
    for block in position.get("details") or []:
        if not block or not block.get("name"):
            continue
        if _looks_like_recruiter_note_block(block["name"]):
            continue  # surfaced separately via position_recruiter_notes
        value = _strip_html(block.get("value") or "")
        if not value:
            continue
        lines.append("")
        lines.append(f"--- {block['name']} ---")
        lines.append(value)
    text = "\n".join(lines).strip()
    return text or "No description provided; infer from title and department only."


def position_recruiter_notes(position: dict[str, Any]) -> str:
    """Pull out the recruiter-added 'Notes/Internal/etc' blocks from
    position.details[]. Returned as a single labeled text section so the
    scoring prompt can call them out distinctly from the formal JD.

    Returns "" when no such blocks exist.
    """
    parts: list[str] = []
    for block in position.get("details") or []:
        if not block or not block.get("name"):
            continue
        if not _looks_like_recruiter_note_block(block["name"]):
            continue
        value = _strip_html(block.get("value") or "")
        if not value:
            continue
        parts.append(f"--- {block['name']} ---")
        parts.append(value)
    if not parts:
        return ""
    return (
        "[POSITION-LEVEL RECRUITER NOTES — the hiring manager / recruiter added "
        "these to the role itself. Treat them as overrides on the formal JD when "
        "they conflict.]\n"
        + "\n".join(parts)
    )


__all__ = [
    "ComeetClient",
    "ComeetError",
    "ComeetBandwidthError",
    "ComeetTransientError",
    "candidate_active_for_screening",
    "candidate_in_allowed_step",
    "candidate_max_activity_iso",
    "candidate_full_name",
    "position_country",
    "position_full_location",
    "position_lead_recruiter",
    "position_jd_text",
    "mint_token",
]
