"""arXiv paper source: metadata via the arXiv Atom API, full text via the PDF.

* Always HTTPS — ``http://export.arxiv.org`` is documented to return empty
  bodies.
* arXiv ids are validated (new ``YYMM.NNNNN`` and old ``archive/NNNNNNN`` forms,
  with or without a ``vN`` suffix or an ``arxiv.org/abs|pdf/`` wrapper).
* Network calls retry a few times with exponential backoff on timeouts / 5xx /
  429; a hard failure raises a descriptive error. If only the *PDF* fails the
  paper is still returned (metadata + abstract, ``raw_text=None``) — the
  abstract alone is useful for triage.
"""

from __future__ import annotations

import time

from strata_mcp.core.dedupe import normalize_arxiv_id
from strata_mcp.core.entities import coerce_year
from strata_mcp.core.ports import FetchedPaper, IPaperSource, SearchHit, SearchResult

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
_USER_AGENT = "strata-mcp/0.1 (+https://github.com/vjrivmon/strata-mcp)"
_RETRIES = 3
_BACKOFF_BASE = 1.5


class ArxivError(RuntimeError):
    """An arXiv lookup / search failed."""


def parse_arxiv_id(ref: str) -> str | None:
    """The bare arXiv id for ``ref`` (id, ``arXiv:`` form, or abs/pdf URL), or
    ``None`` if it is not an arXiv reference."""
    return normalize_arxiv_id(ref)


def _http_get(url: str, params: dict | None = None, *, timeout: float = 30.0) -> bytes:
    import httpx

    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                resp = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
            if resp.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError("retryable status", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < _RETRIES - 1:
                time.sleep(_BACKOFF_BASE**attempt)
    raise ArxivError(f"GET {url} failed after {_RETRIES} attempts: {last_exc}")


def _parse_feed(raw: bytes) -> list[dict]:
    import feedparser

    feed = feedparser.parse(raw)
    entries: list[dict] = []
    for e in feed.entries:
        arxiv_id = normalize_arxiv_id(getattr(e, "id", None))
        doi = getattr(e, "arxiv_doi", None)
        venue = getattr(e, "arxiv_journal_ref", None)
        year = None
        for key in ("published_parsed", "updated_parsed"):
            tm = getattr(e, key, None)
            if tm is not None:
                year = tm.tm_year
                break
        authors = [a.get("name", "").strip() for a in getattr(e, "authors", []) if a.get("name")]
        entries.append(
            {
                "title": " ".join((getattr(e, "title", "") or "").split()).strip(),
                "abstract": " ".join((getattr(e, "summary", "") or "").split()).strip() or None,
                "authors": authors,
                "year": coerce_year(year),
                "arxiv_id": arxiv_id,
                "doi": doi or None,
                "venue": venue or None,
                "url": f"https://arxiv.org/abs/{arxiv_id}"
                if arxiv_id
                else getattr(e, "link", None),
            }
        )
    return entries


class ArxivSource(IPaperSource):
    name = "arxiv"

    def can_handle(self, ref: str) -> bool:
        return parse_arxiv_id(ref) is not None

    def fetch(self, ref: str) -> FetchedPaper:
        arxiv_id = parse_arxiv_id(ref)
        if not arxiv_id:
            raise ArxivError(f"not an arXiv reference: {ref!r}")
        raw = _http_get(ARXIV_API_URL, {"id_list": arxiv_id, "max_results": 1})
        entries = _parse_feed(raw)
        if not entries or not entries[0]["title"]:
            raise ArxivError(f"arXiv paper not found: {arxiv_id}")
        meta = entries[0]

        raw_text: str | None = None
        truncated: str | None = None
        try:
            from strata_mcp.adapters.pdf_extractor import extract_pdf_text, truncate_words

            pdf_bytes = _http_get(ARXIV_PDF_URL.format(arxiv_id=arxiv_id))
            raw_text = extract_pdf_text(pdf_bytes, source_hint=f"arXiv:{arxiv_id}")
            truncated = truncate_words(raw_text)
        except Exception:  # noqa: BLE001 - metadata + abstract is still a usable result
            raw_text = None
            truncated = None

        return FetchedPaper(
            title=meta["title"],
            doi=meta["doi"],
            arxiv_id=arxiv_id,
            authors=meta["authors"],
            year=meta["year"],
            venue=meta["venue"],
            url=meta["url"] or f"https://arxiv.org/abs/{arxiv_id}",
            abstract=meta["abstract"],
            raw_text=raw_text,
            raw_text_truncated=truncated,
            source=self.name,
        )

    def search(self, query: str, max_results: int = 20) -> SearchResult:
        query = (query or "").strip()
        if not query:
            return SearchResult(query=query, hits=[], error="empty query")
        n = max(1, min(int(max_results), 100))
        try:
            raw = _http_get(
                ARXIV_API_URL,
                {
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": n,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
            )
        except ArxivError as exc:
            return SearchResult(query=query, hits=[], error=str(exc))
        hits = [
            SearchHit(
                title=e["title"],
                abstract=e["abstract"],
                authors=e["authors"],
                year=e["year"],
                arxiv_id=e["arxiv_id"],
                doi=e["doi"],
                url=e["url"],
                source=self.name,
            )
            for e in _parse_feed(raw)
            if e["title"]
        ]
        return SearchResult(query=query, hits=hits)
