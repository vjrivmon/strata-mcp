"""Paper deduplication.

A paper is identified by a hierarchical dedupe key:

    1. DOI (normalised)                         -- strongest
    2. arXiv id without version suffix
    3. normalised URL
    4. normalised title                          -- last resort

Two records with the same key are the same paper (the storage adapter upserts).
When matching *only* by title, a v1 refinement also checks first author + year
to avoid merging real homonyms (see EDGE-CASES 3.4).

Scaffold note: the trivial normalisers are implemented; the matching policy
(``dedupe_key``, ``same_paper``) is wired in phase 6.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "src",
}


def normalize_title(title: str) -> str:
    """Lowercase, strip LaTeX-ish noise, collapse whitespace, drop punctuation."""
    t = unicodedata.normalize("NFKC", title or "")
    # crude de-LaTeX: drop $...$ math and \command{...} wrappers' braces
    t = re.sub(r"\$[^$]*\$", " ", t)
    t = t.replace("{", " ").replace("}", " ").replace("\\", " ")
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip().lower()
    return t


def normalize_doi(doi: str | None) -> str | None:
    """Strip ``https://doi.org/`` / ``doi:`` prefixes, lowercase."""
    if not doi:
        return None
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix) :]
    return d.strip("/") or None


def normalize_arxiv_id(arxiv_id: str | None) -> str | None:
    """Strip URL wrapping and the ``vN`` version suffix; keep the bare id."""
    if not arxiv_id:
        return None
    a = arxiv_id.strip().lower()
    a = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", a)
    a = re.sub(r"\.pdf$", "", a)
    a = _ARXIV_VERSION.sub("", a)
    return a or None


def normalize_url(url: str | None) -> str | None:
    """Drop fragment, tracking query params and trailing slash; lowercase host."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return url.strip() or None
    query = "&".join(
        kv
        for kv in parts.query.split("&")
        if kv and kv.split("=", 1)[0].lower() not in _TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def dedupe_key(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    url: str | None = None,
    title: str | None = None,
) -> str:
    """Compute the hierarchical dedupe key. Raises ``ValueError`` if nothing
    identifying is supplied. Implemented in phase 6."""
    raise NotImplementedError


def same_paper(a: dict, b: dict) -> bool:
    """True if two paper-like dicts refer to the same work. Implemented in
    phase 6 (uses ``dedupe_key`` plus the title+author+year tie-break)."""
    raise NotImplementedError
