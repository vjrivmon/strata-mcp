"""Generic web paper source: scrape a paper landing page for metadata + text.

Uses defensive selectors plus ``<meta>`` fallbacks (Dublin Core, Highwire,
Open Graph) so a layout change degrades to minimal metadata instead of
crashing. If the page is clearly not a paper (no abstract, no academic title)
or it's paywalled (only an abstract), that is reported rather than guessed.

Scaffold note: signatures only; implementation in phase 7 (v1 scope).
"""

from __future__ import annotations

from strata_mcp.core.ports import FetchedPaper, IPaperSource


class WebScraperSource(IPaperSource):
    name = "web"

    def can_handle(self, ref: str) -> bool:
        raise NotImplementedError

    def fetch(self, ref: str) -> FetchedPaper:
        raise NotImplementedError
