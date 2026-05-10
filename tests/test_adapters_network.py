"""Network-facing paths of the adapters, with HTTP mocked (``respx``): the
arXiv Atom API + PDF download in :mod:`strata_mcp.adapters.arxiv`, the PDF
download in :mod:`strata_mcp.adapters.pdf_extractor`, and the
``strata_fetch_paper_text`` MCP tool going through them. No real network."""

from __future__ import annotations

import httpx
import pytest
import respx

from strata_mcp import server as srv
from strata_mcp.adapters import arxiv as A
from strata_mcp.adapters import pdf_extractor as P

ARXIV_API = "https://export.arxiv.org/api/query"

_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v5</id>
    <updated>2017-12-06T03:30:00Z</updated>
    <published>2017-06-12T17:00:00Z</published>
    <title>Attention Is All You Need</title>
    <summary> The dominant sequence transduction models ... we propose the Transformer. </summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:journal_ref>NeurIPS 2017</arxiv:journal_ref>
    <arxiv:doi>10.5555/3295222.3295349</arxiv:doi>
    <link href="http://arxiv.org/abs/1706.03762v5" rel="alternate" type="text/html"/>
  </entry>
</feed>"""

_FEED_EMPTY = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # the retry backoff in arxiv._http_get must not actually sleep in tests
    monkeypatch.setattr(A.time, "sleep", lambda *_a, **_k: None)


# --------------------------------------------------------------------------- #
# ArxivSource.fetch
# --------------------------------------------------------------------------- #
@respx.mock
def test_arxiv_fetch_success(sample_pdf_bytes):
    respx.get(ARXIV_API).mock(return_value=httpx.Response(200, content=_FEED))
    respx.get("https://arxiv.org/pdf/1706.03762").mock(
        return_value=httpx.Response(200, content=sample_pdf_bytes)
    )
    fp = A.ArxivSource().fetch("https://arxiv.org/abs/1706.03762")
    assert fp.title == "Attention Is All You Need"
    assert fp.arxiv_id == "1706.03762"
    assert fp.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert fp.year == 2017 and fp.venue == "NeurIPS 2017"
    assert fp.doi == "10.5555/3295222.3295349"
    assert fp.url == "https://arxiv.org/abs/1706.03762"
    assert fp.abstract and "Transformer" in fp.abstract
    assert fp.raw_text and "abstract" in fp.raw_text.lower()
    assert fp.raw_text_truncated == fp.raw_text  # short -> not truncated
    assert fp.source == "arxiv"


@respx.mock
def test_arxiv_fetch_pdf_failure_degrades_to_metadata():
    respx.get(ARXIV_API).mock(return_value=httpx.Response(200, content=_FEED))
    respx.get("https://arxiv.org/pdf/1706.03762").mock(return_value=httpx.Response(404))
    fp = A.ArxivSource().fetch("1706.03762")
    assert fp.title == "Attention Is All You Need" and fp.abstract
    assert fp.raw_text is None and fp.raw_text_truncated is None  # PDF failed, metadata kept


@respx.mock
def test_arxiv_fetch_not_found():
    respx.get(ARXIV_API).mock(return_value=httpx.Response(200, content=_FEED_EMPTY))
    with pytest.raises(A.ArxivError, match="not found"):
        A.ArxivSource().fetch("1706.03762")


def test_arxiv_fetch_rejects_non_arxiv_ref():
    with pytest.raises(A.ArxivError, match="not an arXiv"):
        A.ArxivSource().fetch("https://example.com/foo.pdf")


@respx.mock
def test_arxiv_api_retries_then_raises():
    route = respx.get(ARXIV_API).mock(return_value=httpx.Response(503))
    with pytest.raises(A.ArxivError, match="after 3 attempts"):
        A.ArxivSource().fetch("1706.03762")
    assert route.call_count == 3


@respx.mock
def test_arxiv_api_recovers_on_retry(sample_pdf_bytes):
    respx.get(ARXIV_API).mock(side_effect=[httpx.Response(503), httpx.Response(200, content=_FEED)])
    respx.get("https://arxiv.org/pdf/1706.03762").mock(
        return_value=httpx.Response(200, content=sample_pdf_bytes)
    )
    fp = A.ArxivSource().fetch("1706.03762")
    assert fp.title == "Attention Is All You Need"


# --------------------------------------------------------------------------- #
# ArxivSource.search
# --------------------------------------------------------------------------- #
@respx.mock
def test_arxiv_search_success():
    respx.get(ARXIV_API).mock(return_value=httpx.Response(200, content=_FEED))
    res = A.ArxivSource().search("transformer attention", max_results=5)
    assert res.error is None and len(res.hits) == 1
    h = res.hits[0]
    assert h.title == "Attention Is All You Need" and h.arxiv_id == "1706.03762"
    assert h.year == 2017 and h.source == "arxiv"


@respx.mock
def test_arxiv_search_error_returns_empty_with_message():
    respx.get(ARXIV_API).mock(return_value=httpx.Response(503))
    res = A.ArxivSource().search("anything")
    assert res.hits == [] and res.error and "attempts" in res.error


def test_arxiv_search_empty_query():
    res = A.ArxivSource().search("   ")
    assert res.hits == [] and res.error == "empty query"


# --------------------------------------------------------------------------- #
# pdf_extractor._download
# --------------------------------------------------------------------------- #
@respx.mock
def test_pdf_download_success_and_failure():
    respx.get("https://host/paper.pdf").mock(return_value=httpx.Response(200, content=b"%PDF-..."))
    assert P._download("https://host/paper.pdf") == b"%PDF-..."
    respx.get("https://host/missing.pdf").mock(return_value=httpx.Response(404))
    with pytest.raises(P.PdfError, match="could not download"):
        P._download("https://host/missing.pdf")


@respx.mock
def test_pdf_source_fetch_url(sample_pdf_bytes):
    respx.get("https://host/great_paper.pdf").mock(
        return_value=httpx.Response(200, content=sample_pdf_bytes)
    )
    fp = P.PdfSource().fetch("https://host/great_paper.pdf")
    assert fp.source == "pdf" and fp.raw_text and "abstract" in fp.raw_text.lower()
    assert fp.url == "https://host/great_paper.pdf"
    assert fp.title
    # a non-PDF body from a .pdf URL -> hard fail
    respx.get("https://host/notreally.pdf").mock(
        return_value=httpx.Response(200, content=b"<html/>")
    )
    with pytest.raises(P.PdfError):
        P.PdfSource().fetch("https://host/notreally.pdf")


# --------------------------------------------------------------------------- #
# the strata_fetch_paper_text MCP tool, end to end through the adapters
# --------------------------------------------------------------------------- #
@respx.mock
def test_strata_fetch_paper_text_tool_arxiv(sample_pdf_bytes):
    respx.get(ARXIV_API).mock(return_value=httpx.Response(200, content=_FEED))
    respx.get("https://arxiv.org/pdf/1706.03762").mock(
        return_value=httpx.Response(200, content=sample_pdf_bytes)
    )
    out = srv.strata_fetch_paper_text("https://arxiv.org/abs/1706.03762")
    assert out["title"] == "Attention Is All You Need"
    assert out["arxiv_id"] == "1706.03762" and out["source"] == "arxiv"
    assert out["raw_text"] and out["raw_text_truncated"]
    assert out["doi"] == "10.5555/3295222.3295349"


@respx.mock
def test_strata_fetch_paper_text_tool_pdf(tmp_path, sample_pdf_bytes):
    respx.get("https://host/x.pdf").mock(return_value=httpx.Response(200, content=sample_pdf_bytes))
    out = srv.strata_fetch_paper_text("https://host/x.pdf", hint="pdf")
    assert out["source"] == "pdf" and out["raw_text"]
