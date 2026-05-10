"""Paper deduplication.

A paper is identified by a hierarchical dedupe key, strongest signal first:

    1. ``doi:<normalised-doi>``        -- canonical, unambiguous
    2. ``arxiv:<id-without-version>``  -- canonical for arXiv works
    3. ``url:<normalised-url>``        -- a stable landing page
    4. ``title:<normalised-title>``    -- last resort

Two records with the same key are treated as the same paper, so the storage
adapter upserts on it. :func:`same_paper` is the softer comparison the scout
uses ("is this candidate already in my library?"): it trusts a matching strong
identifier, never declares two papers different just because their URLs differ,
and when it can only compare titles it requires a non-contradicting first
author / year before merging (avoids fusing real homonyms — EDGE-CASES 3.4).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
_ARXIV_NEW_ID = re.compile(r"^\d{4}\.\d{4,5}$")
_ARXIV_OLD_ID = re.compile(r"^[a-z][a-z.\-]+/\d{7}$")
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "src",
    "fbclid",
    "gclid",
}


# --------------------------------------------------------------------------- #
# Normalisers
# --------------------------------------------------------------------------- #
def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_title(title: str | None) -> str:
    """Lowercase, strip LaTeX-ish noise, drop punctuation, collapse whitespace.

    Returns ``""`` for falsy / whitespace-only input so callers can treat "no
    usable title" uniformly.
    """
    t = unicodedata.normalize("NFKC", title or "")
    # crude de-LaTeX: drop $...$ math, then unwrap \command{...} braces
    t = re.sub(r"\$[^$]*\$", " ", t)
    t = t.replace("{", " ").replace("}", " ").replace("\\", " ")
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip().lower()
    return t


def normalize_doi(doi: str | None) -> str | None:
    """Strip ``https://doi.org/`` / ``doi:`` wrappers and lowercase; ``None`` if
    nothing usable remains."""
    if not doi:
        return None
    d = doi.strip().lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if d.startswith(prefix):
            d = d[len(prefix) :]
            break
    d = d.strip().strip("/")
    return d or None


def normalize_arxiv_id(arxiv_id: str | None) -> str | None:
    """Strip URL wrapping, ``.pdf`` and the ``vN`` version suffix; keep the bare
    id. ``None`` if it does not look like an arXiv identifier."""
    if not arxiv_id:
        return None
    a = arxiv_id.strip().lower()
    a = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", a)
    a = re.sub(r"\?.*$", "", a)
    a = re.sub(r"\.pdf$", "", a)
    if a.startswith("arxiv:"):
        a = a[len("arxiv:") :]
    a = _ARXIV_VERSION.sub("", a).strip("/")
    if not a:
        return None
    if _ARXIV_NEW_ID.match(a) or _ARXIV_OLD_ID.match(a):
        return a
    return None


def normalize_url(url: str | None) -> str | None:
    """Drop the fragment and tracking query params, lowercase the host, trim a
    trailing slash. Returns the input stripped (or ``None``) when it is not a
    parseable absolute URL, so it never silently loses information."""
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return raw
    query = "&".join(
        kv
        for kv in parts.query.split("&")
        if kv and kv.split("=", 1)[0].lower() not in _TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def first_author_surname(authors: Iterable[str] | None) -> str | None:
    """Best-effort surname of the first author, accent- and case-folded.

    Handles both ``"Vicente Rivas Monferrer"`` (surname = last token) and
    ``"Rivas Monferrer, Vicente"`` (surname = part before the comma).
    """
    if not authors:
        return None
    first = next((a for a in authors if a and a.strip()), None)
    if not first:
        return None
    chunk = first.split(",", 1)[0] if "," in first else first
    tokens = _WS.sub(" ", _PUNCT.sub(" ", _strip_accents(chunk))).strip().lower().split()
    return tokens[-1] if tokens else None


# --------------------------------------------------------------------------- #
# Keys & matching
# --------------------------------------------------------------------------- #
def dedupe_key(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    url: str | None = None,
    title: str | None = None,
) -> str:
    """Compute the hierarchical dedupe key (``doi:`` > ``arxiv:`` > ``url:`` >
    ``title:``).

    Raises :class:`ValueError` when nothing identifying is supplied — a paper
    with no DOI, arXiv id, URL *and* no title cannot be stored.
    """
    d = normalize_doi(doi)
    if d:
        return f"doi:{d}"
    a = normalize_arxiv_id(arxiv_id)
    if a:
        return f"arxiv:{a}"
    u = normalize_url(url)
    if u:
        return f"url:{u}"
    t = normalize_title(title)
    if t:
        return f"title:{t}"
    raise ValueError("cannot build a dedupe key: need at least one of doi, arxiv_id, url, title")


def dedupe_key_for(paper: object) -> str:
    """:func:`dedupe_key` for any object/dict exposing ``doi`` / ``arxiv_id`` /
    ``url`` / ``title``."""
    get = paper.get if isinstance(paper, dict) else lambda k: getattr(paper, k, None)
    return dedupe_key(doi=get("doi"), arxiv_id=get("arxiv_id"), url=get("url"), title=get("title"))


def _norm_year(value: object) -> int | None:
    try:
        y = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return y if 1500 <= y <= 2200 else None


def same_paper(a: dict, b: dict) -> bool:
    """True if two paper-like dicts refer to the same work.

    A matching strong identifier (DOI, then arXiv id) is decisive in both
    directions; a matching normalised URL confirms a match but a *mismatch*
    does not (a paper lives at both arxiv.org and the publisher); otherwise the
    titles must match *and* not be contradicted by a differing year or first
    author surname.
    """
    for norm, key in ((normalize_doi, "doi"), (normalize_arxiv_id, "arxiv_id")):
        va, vb = norm(a.get(key)), norm(b.get(key))
        if va and vb:
            return va == vb

    ua, ub = normalize_url(a.get("url")), normalize_url(b.get("url"))
    if ua and ub and ua == ub:
        return True

    ta, tb = normalize_title(a.get("title")), normalize_title(b.get("title"))
    if not ta or not tb or ta != tb:
        return False

    ya, yb = _norm_year(a.get("year")), _norm_year(b.get("year"))
    if ya and yb and ya != yb:
        return False  # same title, different year -> likely homonyms

    fa, fb = first_author_surname(a.get("authors")), first_author_surname(b.get("authors"))
    if fa and fb:
        return fa == fb

    return True  # identical title, nothing contradicts it
