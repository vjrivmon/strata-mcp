"""Generic web paper source: scrape a paper landing page for metadata + text.

Reads the standard scholarly ``<meta>`` tags first — Highwire ``citation_*``
(what Google Scholar, journals and conference sites emit), Dublin Core
``DC.*``/``DCTERMS.*``, then Open Graph and the plain ``description``/``author``
tags as fallbacks — so a site redesign degrades to minimal metadata instead of
crashing. The body text comes from ``<article>`` / ``<main>`` / a content
``<div>`` (scripts, nav, header/footer stripped). A page that clearly is not a
paper (no usable title and almost no text) is reported, not guessed at; a
paywalled page that only exposes an abstract still yields a useful record
(``raw_text=None``, abstract kept) — same contract as the arXiv adapter when
only the PDF fails.

This adapter is the dispatch catch-all for ``http(s)`` URLs: arXiv, direct PDFs
and Semantic Scholar are matched by their own adapters first.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from strata_mcp.adapters import _http
from strata_mcp.core.dedupe import normalize_arxiv_id
from strata_mcp.core.entities import coerce_year, normalize_authors
from strata_mcp.core.ports import FetchedPaper, IPaperSource

_MIN_PAPER_CHARS = 400  # below this, with no title/abstract, "not a paper"
_MAX_TEXT_CHARS = 200_000
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s\"'<>?#]+)", re.IGNORECASE)
_HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


class WebScrapeError(RuntimeError):
    """A web page could not be fetched or did not look like a paper."""


def _looks_like_web_ref(ref: str) -> bool:
    parts = urlsplit((ref or "").strip())
    return parts.scheme in ("http", "https") and not parts.path.lower().endswith(".pdf")


def _meta(soup, **attrs) -> str | None:
    """The ``content`` of the first ``<meta>`` matching ``attrs`` (each value
    matched case-insensitively against ``name``/``property``/``http-equiv``)."""
    for tag in soup.find_all("meta"):
        for key, want in attrs.items():
            got = tag.get(key) or tag.get(key.replace("_", "-"))
            if got and got.strip().lower() == want.lower():
                content = (tag.get("content") or "").strip()
                return content or None
    return None


def _meta_any(soup, names: list[str]) -> str | None:
    """First non-empty ``content`` among ``<meta name=...>`` / ``property=...``
    for the given names (in order)."""
    for n in names:
        for attr in ("name", "property", "http-equiv", "itemprop"):
            val = _meta(soup, **{attr: n})
            if val:
                return val
    return None


def _meta_all(soup, names: list[str]) -> list[str]:
    """Every ``content`` for ``<meta name=...>`` / ``property=...`` matching any
    of ``names`` (Highwire emits one ``citation_author`` tag per author)."""
    wanted = {n.lower() for n in names}
    out: list[str] = []
    for tag in soup.find_all("meta"):
        ident = (
            (tag.get("name") or tag.get("property") or tag.get("itemprop") or "").strip().lower()
        )
        if ident in wanted:
            content = (tag.get("content") or "").strip()
            if content:
                out.append(content)
    return out


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return " ".join(str(text).split()).strip() or None


def _extract_title(soup) -> str | None:
    title = _meta_any(soup, ["citation_title", "dc.title", "dcterms.title", "og:title", "title"])
    if title:
        return _clean(title)
    if soup.title and soup.title.string:
        return _clean(soup.title.string)
    h1 = soup.find("h1")
    return _clean(h1.get_text()) if h1 else None


def _extract_abstract(soup) -> str | None:
    abs = _meta_any(
        soup,
        [
            "citation_abstract",
            "dcterms.abstract",
            "dc.description",
            "description",
            "og:description",
            "twitter:description",
        ],
    )
    if abs:
        return _clean(abs)
    node = soup.find(
        lambda t: (
            t.name in ("div", "section", "p")
            and re.search(
                r"\babstract\b", " ".join(t.get("class") or []) + " " + (t.get("id") or ""), re.I
            )
        )
    )
    return _clean(node.get_text(" ")) if node else None


def _extract_authors(soup) -> list[str]:
    raw = _meta_all(soup, ["citation_author", "dc.creator", "dcterms.creator", "parsely-author"])
    if not raw:
        single = _meta_any(soup, ["author", "article:author", "citation_authors", "dc.contributor"])
        if single:
            raw = re.split(r"\s*[;,]\s*|\s+and\s+", single)
    return normalize_authors(raw)


def _extract_year(soup) -> int | None:
    raw = _meta_any(
        soup,
        [
            "citation_publication_date",
            "citation_date",
            "citation_year",
            "dc.date",
            "dcterms.issued",
            "dcterms.date",
            "article:published_time",
            "datepublished",
        ],
    )
    if raw:
        m = re.search(r"\d{4}", raw)
        if m:
            y = coerce_year(m.group(0))
            if y:
                return y
    t = soup.find("time")
    if t and (t.get("datetime") or t.get_text()):
        m = re.search(r"\d{4}", t.get("datetime") or t.get_text())
        if m:
            return coerce_year(m.group(0))
    return None


def _extract_doi(soup, html_text: str) -> str | None:
    raw = _meta_any(
        soup, ["citation_doi", "dc.identifier", "dcterms.identifier", "doi", "prism.doi"]
    )
    if raw:
        m = _DOI_RE.search(raw)
        if m:
            return m.group(0).rstrip(".,;)")
    for a in soup.find_all("a", href=True):
        m = _DOI_RE.search(a["href"])
        if m:
            return m.group(0).rstrip(".,;)")
    m = _DOI_RE.search(html_text)
    return m.group(0).rstrip(".,;)") if m else None


def _extract_arxiv_id(soup, html_text: str) -> str | None:
    raw = _meta_any(soup, ["citation_arxiv_id", "citation_arxiv", "arxiv_id"])
    if raw:
        norm = normalize_arxiv_id(raw)
        if norm:
            return norm
    for a in soup.find_all("a", href=True):
        m = _ARXIV_URL_RE.search(a["href"])
        if m:
            norm = normalize_arxiv_id(m.group(1))
            if norm:
                return norm
    m = _ARXIV_URL_RE.search(html_text)
    return normalize_arxiv_id(m.group(1)) if m else None


def _extract_venue(soup) -> str | None:
    return _clean(
        _meta_any(
            soup,
            [
                "citation_journal_title",
                "citation_conference_title",
                "citation_inbook_title",
                "prism.publicationname",
                "dc.source",
                "dcterms.ispartof",
                "og:site_name",
            ],
        )
    )


def _extract_body_text(soup) -> str:
    for tag in soup(
        ["script", "style", "noscript", "template", "nav", "header", "footer", "aside", "form"]
    ):
        tag.decompose()
    container = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find(
            lambda t: (
                t.name == "div"
                and re.search(
                    r"content|article|post|paper|fulltext|body",
                    " ".join(t.get("class") or []) + " " + (t.get("id") or ""),
                    re.I,
                )
            )
        )
        or soup.body
        or soup
    )
    text = container.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:_MAX_TEXT_CHARS]


class WebScraperSource(IPaperSource):
    name = "web"

    def can_handle(self, ref: str) -> bool:
        return _looks_like_web_ref(ref)

    def fetch(self, ref: str) -> FetchedPaper:
        ref = (ref or "").strip()
        if not _looks_like_web_ref(ref):
            raise WebScrapeError(f"not a scrapable http(s) page: {ref!r}")
        try:
            data = _http.get_bytes(ref, headers={"Accept": _HTML_ACCEPT})
        except _http.HttpError as exc:
            raise WebScrapeError(f"could not fetch {ref}: {exc}") from exc

        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:  # pragma: no cover - beautifulsoup4 is a hard dependency
            raise WebScrapeError("beautifulsoup4 is not installed") from exc

        html_text = data.decode("utf-8", errors="replace")
        soup = BeautifulSoup(data, "html.parser")

        # Read metadata before _extract_body_text mutates the tree.
        title = _extract_title(soup)
        abstract = _extract_abstract(soup)
        authors = _extract_authors(soup)
        year = _extract_year(soup)
        doi = _extract_doi(soup, html_text)
        arxiv_id = _extract_arxiv_id(soup, html_text)
        venue = _extract_venue(soup)

        body_text = _extract_body_text(soup)

        if not title:
            if not abstract and len(body_text) < _MIN_PAPER_CHARS:
                raise WebScrapeError(
                    f"{ref}: no usable title and almost no text — this does not look like a paper page"
                )
            # Substantial content but no <title>: fall back to the URL slug so the
            # Paper entity (which requires a title) still accepts it.
            slug = urlsplit(ref).path.rstrip("/").rsplit("/", 1)[-1] or urlsplit(ref).netloc
            title = _clean(slug.replace("-", " ").replace("_", " ")) or ref

        raw_text = body_text or None
        truncated: str | None = None
        if raw_text:
            from strata_mcp.adapters.pdf_extractor import truncate_words

            truncated = truncate_words(raw_text)

        return FetchedPaper(
            title=title,
            doi=doi,
            arxiv_id=arxiv_id,
            authors=authors,
            year=year,
            venue=venue,
            url=ref,
            abstract=abstract,
            raw_text=raw_text,
            raw_text_truncated=truncated,
            source="web",
        )
