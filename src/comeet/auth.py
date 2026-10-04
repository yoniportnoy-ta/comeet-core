"""Comeet auth: HS256 JWT minting and credential discovery.

The token algorithm is the same one Code.gs uses — HS256 over {iss: api_key},
valid ten minutes. Credentials come from the environment first and dotenv
files second, so a laptop and a container can share this code unchanged.
"""
from __future__ import annotations

import logging
import os
import time
from typing import List, Optional, Tuple

import jwt

log = logging.getLogger(__name__)


def mint_token(api_key: str, api_secret: str, *, ttl_seconds: int = 600) -> str:
    """Mint an HS256 JWT for api.comeet.co. Same algorithm as Code.gs."""
    now = int(time.time())
    payload = {"iss": api_key, "exp": now + ttl_seconds}
    return jwt.encode(payload, api_secret, algorithm="HS256")





CREDENTIAL_FILE_CANDIDATES: List[str] = [
    os.path.expanduser("~/.config/comeet/.env"),
    os.path.expanduser("~/.comeet.env"),
]


def _parse_dotenv_line(line: str) -> Optional[Tuple[str, str]]:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s.startswith("export "):
        s = s[len("export "):].lstrip()
    if "=" not in s:
        return None
    k, _, v = s.partition("=")
    k = k.strip()
    v = v.strip()
    if v and v[0] in {"'", '"'}:
        # Quoted: take what is inside the matching quote and discard any
        # trailing comment. The previous version only stripped comments from
        # UNQUOTED values, so KEY="abc" # note yielded '"abc" # note'.
        q = v[0]
        end = v.find(q, 1)
        v = v[1:end] if end > 0 else v[1:]
    elif "#" in v:
        v = v.split("#", 1)[0].rstrip()
    return (k, v) if k else None


def load_comeet_credentials(
    extra_paths: Optional[List[str]] = None,
) -> Tuple[str, str, str]:
    key = os.getenv("COMEET_API_KEY", "").strip()
    secret = os.getenv("COMEET_API_SECRET", "").strip()
    used_sources: List[str] = []
    if key and secret:
        return key, secret, "env"
    if key or secret:
        used_sources.append("env")

    paths = list(CREDENTIAL_FILE_CANDIDATES)
    if extra_paths:
        paths.extend(extra_paths)

    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        found_here = False
        for raw in lines:
            parsed = _parse_dotenv_line(raw)
            if parsed is None:
                continue
            k, v = parsed
            if k == "COMEET_API_KEY" and not key and v:
                key = v
                found_here = True
            elif k == "COMEET_API_SECRET" and not secret and v:
                secret = v
                found_here = True
        if found_here:
            used_sources.append(f"file:{path}")
        if key and secret:
            break

    return key, secret, ",".join(used_sources) or "missing"
