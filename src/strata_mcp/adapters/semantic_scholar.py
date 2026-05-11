"""Semantic Scholar paper source: metadata + search via the Graph API.

Semantic Scholar has no full text, but it resolves a DOI / arXiv id / CorpusID /
its own paperId to clean metadata + an abstract, and it points at an open-access
PDF when one exists — which we then run through the PDF extractor for ``raw_text``
(best effort; a paywalled or missing PDF just leaves ``raw_text=None``).

The public API is keyless and aggressively rate-limited (it 429s readily — ~1
request/second is the unofficial budget). :mod:`strata_mcp.adapters._http`
already retries 429 / 5xx with exponential backoff; on top of that, ``search``
degrades to an *empty* :class:`SearchResult` with ``error`` set rather than
raising, so a rate-limited scout run still completes with whatever the other
sources returned.
"""

from __future__ import annotations

import re

from strata_mcp.adapters import _http
from strata_mcp.core.dedupe import normalize_arxiv_id
from strata_mcp.core.entities import coerce_year
from strata_mcp.core.ports import FetchedPaper, IPaperSource, SearchHit, SearchResult

S2_API_URL = "https://api.semanticscholar.org/graph/v1"
_PAPER_FIELDS = "title,abstract,authors,year,venue,externalIds,openAccessPdf,url"
_SEARCH_FIELDS = "title,abstract,authors,year,venue,externalIds,openAccessPdf,paperId"

# 40-char hex = a Semantic Scholar ``paperId``.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
# Bare DOI (the common 10.NNNN/... shape).
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_URL_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/\S+)$", re.IGNORECASE)
_S2_URL_RE = re.compile(
    r"^https?://(?:www\.)?semanticscholar\.org/paper/(?:[^/]+/)?([0-9a-f]{40})\b", re.IGNORECASE
)
_S2_API_PAPER_RE = re.compile(
    r"^https?://api\.semanticscholar\.org/(?:graph/v1/)?paper/([^/?#]+)", re.IGNORECASE
)
_CORPUS_RE = re.compile(r"^corpus[ _]?id:\s*(\d+)$", re.IGNORECASE)
# ``DOI:...`` / ``ARXIV:...`` / ``PMID:...`` etc. — API id prefixes passed through.
_PREFIXED_RE = re.compile(r"^(DOI|ARXIV|MAG|ACL|PMID|PMCID|URL|CorpusId):\S+$", re.IGNORECASE)


class S2Error(RuntimeError):
    """A Semantic Scholar lookup failed (bad reference, not found, API error)."""


def parse_s2_id(ref: str) -> str | None:
    """The Graph-API paper-id segment for ``ref`` (a bare ``paperId`` sha, a
    ``semanticscholar.org/paper/...`` URL, an ``api.semanticscholar.org`` URL, a
    ``CorpusID:N``, a DOI / ``https://doi.org/...``, or an already-prefixed
    ``DOI:``/``ARXIV:``/``PMID:`` id), or ``None`` if Semantic Scholar can't
    resolve ``ref``.

    arXiv references are *not* claimed here — the dedicated arXiv adapter handles
    them and also gets the full text.
    """
    s = (ref or "").strip()
    if not s:
        return None
    if normalize_arxiv_id(s):  # let the arXiv adapter take arXiv ids/URLs
        return None
    m = _S2_URL_RE.match(s) or _S2_API_PAPER_RE.match(s)
    if m:
        return m.group(1)
    m = _CORPUS_RE.match(s)
    if m:
        return f"CorpusID:{m.group(1)}"
    m = _DOI_URL_RE.match(s)
    if m:
        return f"DOI:{m.group(1)}"
    if _SHA_RE.match(s):
        return s.lower()
    if _DOI_RE.match(s):
        return f"DOI:{s}"
    if _PREFIXED_RE.match(s):
        return s
    return None


def _get(path: str, params: dict | None = None) -> object:
    try:
        return _http.get_json(f"{S2_API_URL}/{path.lstrip('/')}", params)
    except _http.HttpError as exc:
        raise S2Error(str(exc)) from exc


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _authors(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(a.get("name", "")).strip() for a in raw if isinstance(a, dict) and a.get("name")]


def _external_ids(paper: dict) -> dict:
    ext = paper.get("externalIds")
    return ext if isinstance(ext, dict) else {}


def _open_access_pdf_text(paper: dict) -> tuple[str | None, str | None]:
    """Best-effort full text from a paper's ``openAccessPdf`` link; ``(None,
    None)`` if there is no link or anything goes wrong (paywalled host,
    HTML-not-PDF, parse failure)."""
    oa = paper.get("openAccessPdf")
    url = oa.get("url") if isinstance(oa, dict) else None
    if not url:
        return None, None
    try:
        from strata_mcp.adapters.pdf_extractor import extract_pdf_text, truncate_words

        data = _http.get_bytes(url)
        text = extract_pdf_text(data, source_hint=f"S2 openAccessPdf {url}")
        return text, truncate_words(text)
    except Exception:  # noqa: BLE001 - a PDF that won't download/parse is fine; abstract still useful
        return None, None


def _to_fetched(paper: dict) -> FetchedPaper:
    title = _clean(paper.get("title"))
    if not title:
        raise S2Error("Semantic Scholar returned a record with no title")
    ext = _external_ids(paper)
    arxiv_id = normalize_arxiv_id(ext.get("ArXiv")) if ext.get("ArXiv") else None
    raw_text, truncated = _open_access_pdf_text(paper)
    url = paper.get("url") or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None)
    return FetchedPaper(
        title=title,
        doi=ext.get("DOI") or None,
        arxiv_id=arxiv_id,
        authors=_authors(paper.get("authors")),
        year=coerce_year(paper.get("year")),
        venue=_clean(paper.get("venue")),
        url=url,
        abstract=_clean(paper.get("abstract")),
        raw_text=raw_text,
        raw_text_truncated=truncated,
        source="semantic_scholar",
    )


def _to_hit(paper: dict) -> SearchHit | None:
    title = _clean(paper.get("title"))
    if not title:
        return None
    ext = _external_ids(paper)
    arxiv_id = normalize_arxiv_id(ext.get("ArXiv")) if ext.get("ArXiv") else None
    oa = paper.get("openAccessPdf")
    oa_url = oa.get("url") if isinstance(oa, dict) else None
    paper_id = paper.get("paperId")
    url = (
        (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None)
        or oa_url
        or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None)
    )
    return SearchHit(
        title=title,
        abstract=_clean(paper.get("abstract")),
        authors=_authors(paper.get("authors")),
        year=coerce_year(paper.get("year")),
        arxiv_id=arxiv_id,
        doi=ext.get("DOI") or None,
        url=url,
        source="semantic_scholar",
    )


class SemanticScholarSource(IPaperSource):
    name = "semantic_scholar"

    def can_handle(self, ref: str) -> bool:
        return parse_s2_id(ref) is not None

    def fetch(self, ref: str) -> FetchedPaper:
        paper_id = parse_s2_id(ref)
        if not paper_id:
            raise S2Error(f"not a Semantic Scholar reference: {ref!r}")
        data = _get(f"paper/{paper_id}", {"fields": _PAPER_FIELDS})
        if not isinstance(data, dict) or not data:
            raise S2Error(f"Semantic Scholar paper not found: {paper_id}")
        return _to_fetched(data)

    def search(self, query: str, max_results: int = 20) -> SearchResult:
        query = (query or "").strip()
        if not query:
            return SearchResult(query=query, hits=[], error="empty query")
        n = max(1, min(int(max_results), 100))
        try:
            data = _get("paper/search", {"query": query, "limit": n, "fields": _SEARCH_FIELDS})
        except S2Error as exc:
            return SearchResult(query=query, hits=[], error=str(exc))
        rows = data.get("data") if isinstance(data, dict) else None
        hits = [h for h in (_to_hit(p) for p in (rows or []) if isinstance(p, dict)) if h]
        return SearchResult(query=query, hits=hits)
