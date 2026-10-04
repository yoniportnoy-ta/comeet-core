"""Résumé bytes in, readable text out — and fetching them safely.

Comeet résumé URLs are presigned S3 links that expire in 15 minutes, so they
are fetched on demand and never stored. The fetch is bounded by a wall-clock
deadline AND a size cap: the per-read timeout alone does not stop a stream
that trickles forever.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Any, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

MAX_BYTES = 12 * 1024 * 1024
DEADLINE_S = 20.0


def extract_docx_text(content: bytes):
    try:
        import io
        from docx import Document  # python-docx
        doc = Document(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        log.info("docx parse failed: %s", exc)
        return None
    parts = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            parts.append(t)
    for table in getattr(doc, "tables", []):
        for row in table.rows:
            cells = [('' if c is None else c.text or '').strip() for c in row.cells]
            line = "  ".join(x for x in cells if x)
            if line:
                parts.append(line)
    text = "\n".join(parts).strip()
    return text[:_CAP] if len(text) > 100 else None


def extract_rtf_text(content: bytes):
    try:
        s = content.decode("latin-1", errors="ignore")
        s = re.sub(r"\\'[0-9a-fA-F]{2}", " ", s)
        s = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", s)
        s = s.replace("{", " ").replace("}", " ").replace("\\", " ")
        s = re.sub(r"[ \t]+", " ", s).strip()
        return s[:_CAP] if len(s) > 200 else None
    except Exception:  # noqa: BLE001
        return None


def extract_doc_binary_text(content: bytes):
    try:
        best = ""
        pattern = re.compile(r"[A-Za-z0-9 ,.;:@()\-\u2013\u2019'/&+%]{40,}")
        for enc in ("utf-16-le", "latin-1"):
            s = content.decode(enc, errors="ignore")
            joined = "\n".join(m.group(0).strip() for m in pattern.finditer(s))
            if len(joined) > len(best):
                best = joined
        best = best.strip()
        return best[:_CAP] if len(best) > 300 else None
    except Exception:  # noqa: BLE001
        return None


def cv_bytes_to_display(content: bytes, mime: str = "", url: str = ""):
    """Return ("pdf", None) when the bytes are a PDF Slack can preview, or
    ("text", extracted) for Word/RTF, or ("unknown", None)."""
    if content.startswith(b"%PDF") or "pdf" in (mime or "").lower() or url.lower().endswith(".pdf"):
        return "pdf", None
    if content.startswith(b"PK\x03\x04") or url.lower().endswith(".docx"):
        return "text", extract_docx_text(content)
    if content.startswith(b"{\\rtf") or url.lower().endswith(".rtf"):
        return "text", extract_rtf_text(content)
    if content.startswith(b"\xd0\xcf\x11\xe0") or url.lower().endswith(".doc"):  # OLE2
        return "text", extract_doc_binary_text(content)
    return "unknown", None


def resume_url(candidate: dict) -> Optional[str]:
    """The presigned résumé URL on a candidate record, or None."""
    res = candidate.get("resume") or {}
    url = res.get("url") if isinstance(res, dict) else None
    return url or None


def filename_from_url(url: str, fallback: str = "CV.pdf") -> str:
    """Comeet puts the original filename in the presigned URL's disposition."""
    m = re.search(r'filename="([^"]+)"', urllib.parse.unquote(url or ""))
    return urllib.parse.unquote(m.group(1)) if m else fallback


def fetch_resume(url: str, *, max_bytes: int = MAX_BYTES,
                 deadline_s: float = DEADLINE_S) -> Optional[bytes]:
    """Download a résumé, bounded by both a size cap and a wall-clock deadline.

    Returns None rather than raising: a missing CV is a normal condition on a
    candidate record, not an error worth unwinding a caller for.
    """
    if not url:
        return None
    deadline = time.monotonic() + deadline_s
    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(5.0, read=12.0),
                          follow_redirects=True) as r:
            if r.status_code != 200:
                log.info("résumé fetch: HTTP %s", r.status_code)
                return None
            chunks, total = [], 0
            for chunk in r.iter_bytes(64 * 1024):
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes or time.monotonic() > deadline:
                    log.info("résumé fetch aborted (size/deadline)")
                    return None
            return b"".join(chunks) or None
    except Exception as exc:  # noqa: BLE001 — a CV is never worth a 500
        log.info("résumé fetch failed: %s", exc)
        return None
