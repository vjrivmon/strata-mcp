"""Semantic Scholar paper source: metadata + search via the Graph API.

Handles the inconsistent 429 rate limiting with aggressive exponential backoff +
jitter and a short-lived per-query cache; degrades to an empty result instead of
crashing when the quota is exhausted (a documented gotcha).

Scaffold note: signatures only; implementation in phase 7 (v1 scope).
"""

from __future__ import annotations

from strata_mcp.core.ports import FetchedPaper, IPaperSource, SearchResult

S2_API_URL = "https://api.semanticscholar.org/graph/v1"


class SemanticScholarSource(IPaperSource):
    name = "semantic_scholar"

    def can_handle(self, ref: str) -> bool:
        raise NotImplementedError

    def fetch(self, ref: str) -> FetchedPaper:
        raise NotImplementedError

    def search(self, query: str, max_results: int = 20) -> SearchResult:
        raise NotImplementedError
