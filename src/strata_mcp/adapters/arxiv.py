"""arXiv paper source: metadata + full text via the arXiv API and abs/pdf pages.

Always uses HTTPS (``https://export.arxiv.org/api/query``) — HTTP returns empty
responses (a documented gotcha). Validates arXiv ids; retries with exponential
backoff on timeouts / rate limiting.

Scaffold note: signatures only; implementation in phase 7 (rewritten clean,
using the old ``apps/backend/infrastructure/scraping/arxiv_scraper.py`` as
reference).
"""

from __future__ import annotations

from strata_mcp.core.ports import FetchedPaper, IPaperSource, SearchResult

ARXIV_API_URL = "https://export.arxiv.org/api/query"


class ArxivSource(IPaperSource):
    name = "arxiv"

    def can_handle(self, ref: str) -> bool:
        raise NotImplementedError

    def fetch(self, ref: str) -> FetchedPaper:
        raise NotImplementedError

    def search(self, query: str, max_results: int = 20) -> SearchResult:
        raise NotImplementedError
