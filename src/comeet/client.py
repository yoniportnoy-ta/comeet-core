"""Comeet Recruiting API client.

One client for every service that talks to Comeet. See QUIRKS.md before
changing anything here — most of this code exists because of a trap that cost
somebody a day.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .auth import load_comeet_credentials, mint_token
from .errors import (
    ComeetBandwidthError,
    ComeetError,
    ComeetTransientError,
)

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.comeet.co"


def _is_transient_status(code: int) -> bool:
    return code in (302, 502, 503, 504)


def _is_bandwidth_response(code: int, body_text: str) -> bool:
    if code == 429:
        return True
    lower = (body_text or "").lower()
    return "bandwidth quota" in lower or "rate of data transfer" in lower




class ComeetClient:
    """Thin async-ready wrapper. We use sync httpx because the screener is
    not heavily concurrent — clarity beats async-everywhere here."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        env_key, env_secret, source = load_comeet_credentials()
        self.api_key = api_key or env_key
        self.api_secret = api_secret or env_secret
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        log.debug("comeet: credentials from %s", source)
        if not self.api_key or not self.api_secret:
            raise ComeetError("COMEET_API_KEY / COMEET_API_SECRET not set")
        self._client = httpx.Client(timeout=timeout, follow_redirects=False)
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._mint_if_needed()

    def __enter__(self) -> ComeetClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- token lifecycle ---------------------------------------------------
    def _mint_if_needed(self) -> None:
        now = time.time()
        # Refresh 60s before expiry to avoid edge races.
        if not self._token or now > (self._token_expires_at - 60):
            self._token = mint_token(self.api_key, self.api_secret, ttl_seconds=600)
            self._token_expires_at = now + 600
            log.debug("comeet: minted fresh token (expires in 10 min)")

    def refresh_token(self) -> None:
        self._token = ""
        self._mint_if_needed()

    # -- low-level request -------------------------------------------------
    @retry(
        retry=retry_if_exception_type((ComeetTransientError, ComeetBandwidthError, httpx.RequestError)),
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    def _request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> httpx.Response:
        self._mint_if_needed()
        url = f"{self.base_url}{path}" if path.startswith("/") else path
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        log.debug("COMEET %s %s", method, path)
        try:
            resp = self._client.request(method, url, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("comeet network error %s %s: %s", method, path, exc)
            raise

        code = resp.status_code
        body = resp.text or ""
        if _is_bandwidth_response(code, body):
            raise ComeetBandwidthError(
                f"{method} {path} bandwidth quota / 429",
                status=code, body=body[:300],
            )
        if _is_transient_status(code):
            raise ComeetTransientError(
                f"{method} {path} transient {code}",
                status=code, body=body[:300],
            )
        if code == 401:
            # The JWT may have raced out; refresh and retry once via tenacity.
            log.info("comeet 401 — refreshing token")
            self.refresh_token()
            raise ComeetTransientError(f"{method} {path} 401 refresh", status=401, body=body[:300])
        if code >= 400:
            raise ComeetError(
                f"{method} {path} -> {code}: {body[:300]}",
                status=code, body=body[:300],
            )
        return resp

    # -- generic verbs ------------------------------------------------------
    @classmethod
    def from_env(cls, **kwargs: Any) -> "ComeetClient":
        """Build from the environment, naming what is missing if it cannot."""
        key, secret, source = load_comeet_credentials()
        missing = [n for n, v in (("COMEET_API_KEY", key),
                                  ("COMEET_API_SECRET", secret)) if not v]
        if missing:
            raise ComeetError(
                f"{' and '.join(missing)} not set (looked in: {source})")
        return cls(api_key=key, api_secret=secret, **kwargs)

    def get(self, path: str, **kwargs: Any) -> Any:
        """GET and decode JSON. For paths without a typed method of their own."""
        return self._request("GET", path, **kwargs).json()

    def post(self, path: str, **kwargs: Any) -> Any:
        """POST and decode JSON, tolerating an empty body."""
        resp = self._request("POST", path, **kwargs)
        return resp.json() if (resp.content or b"").strip() else None

    # -- high-level methods ------------------------------------------------
    def list_positions(self, status: str | None = None) -> list[dict[str, Any]]:
        """Walk all pages of `/positions`, optionally filtered by status.

        `status=None` returns OPEN **and** CLOSED reqs. Closed ones are where the
        completed hiring outcomes live, so anything mining history wants these —
        only the live-feed paths should filter to open.
        """
        out: list[dict[str, Any]] = []
        url: str | None = "/positions?limit=500" + (f"&status={status}" if status else "")
        while url:
            resp = self._request("GET", url)
            data = resp.json()
            positions = data.get("positions", []) if isinstance(data, dict) else []
            out.extend(p for p in positions if p and p.get("uid"))
            next_page = data.get("next_page") if isinstance(data, dict) else None
            url = next_page if next_page else None
        log.info("comeet: %d positions (status=%s)", len(out), status or "any")
        return out

    def list_open_positions(self) -> list[dict[str, Any]]:
        """Walk all pages of `/positions?status=open`. Returns raw position dicts."""
        out: list[dict[str, Any]] = []
        url: str | None = "/positions?status=open&limit=500"
        while url:
            resp = self._request("GET", url) if url.startswith("/") else self._request("GET", url)
            data = resp.json()
            positions = data.get("positions", []) if isinstance(data, dict) else []
            out.extend(p for p in positions if p and p.get("uid"))
            next_page = data.get("next_page") if isinstance(data, dict) else None
            url = next_page if next_page else None
        log.info("comeet: %d open positions", len(out))
        return out

    def get_position(self, position_uid: str) -> dict[str, Any] | None:
        try:
            resp = self._request("GET", f"/positions/{position_uid}")
        except ComeetError as exc:
            if exc.status == 404:
                return None
            raise
        return resp.json() if resp.content else None

    def get_candidate(self, candidate_uid: str) -> dict[str, Any] | None:
        try:
            resp = self._request("GET", f"/candidates/{candidate_uid}")
        except ComeetError as exc:
            if exc.status == 404:
                return None
            raise
        return resp.json() if resp.content else None

    def list_candidates_for_position(self, position_uid: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        url: str | None = f"/positions/{position_uid}/candidates?limit=1000"
        while url:
            resp = self._request("GET", url)
            data = resp.json()
            page = data.get("candidates", []) if isinstance(data, dict) else []
            out.extend(page)
            next_page = data.get("next_page") if isinstance(data, dict) else None
            url = next_page if next_page else None
        return out

    def post_candidate_note(self, candidate_uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /candidates/{uid}/notes — attach a note to a candidate profile.

        Unversioned base is correct (verified 2026-09-06). Never logs the note
        body: notes can contain candidate-sensitive interview detail.
        """
        resp = self._request("POST", f"/candidates/{candidate_uid}/notes", json=payload)
        try:
            return resp.json() if resp.content else {}
        except Exception:  # noqa: BLE001 — some writes return an empty body
            return {}

    def find_duplicates(self, *, email: str = "", first_name: str = "", last_name: str = "",
                        linkedin_url: str = "", phone_number: str = "") -> list[dict[str, Any]]:
        """`/sourcing/candidates/find_duplicates` — used to surface past hiring processes."""
        params: list[tuple[str, str]] = []
        for key, value in (
            ("email", email),
            ("first_name", first_name),
            ("last_name", last_name),
            ("linkedin_url", linkedin_url),
            ("phone_number", phone_number),
        ):
            v = (value or "").strip()
            if v:
                params.append((key, v))
        if not params:
            return []
        try:
            resp = self._request("GET", "/sourcing/candidates/find_duplicates", params=params)
        except ComeetError as exc:
            if exc.status == 400:
                return []
            if exc.status in (401, 403):
                # Permissions issue — caller decides whether to ignore.
                raise
            raise
        data = resp.json()
        return data if isinstance(data, list) else []

