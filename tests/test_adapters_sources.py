"""Offline tests for the paper-source adapters: arXiv id parsing / dispatch and
the PDF extractor's guards and happy path (with a hand-built PDF). The network
calls (``ArxivSource.fetch`` / ``.search`` over HTTP) are covered by the phase-8
end-to-end run, not here."""

from __future__ import annotations

import pytest

from strata_mcp.adapters import arxiv as A
from strata_mcp.adapters import pdf_extractor as P
from strata_mcp.core.ports import IPaperSource


# --------------------------------------------------------------------------- #
# arXiv adapter (offline parts)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("2401.12345", "2401.12345"),
        ("arXiv:2401.12345v2", "2401.12345"),
        ("https://arxiv.org/abs/2401.12345", "2401.12345"),
        ("https://arxiv.org/pdf/2401.12345v3.pdf", "2401.12345"),
        ("hep-th/9901001", "hep-th/9901001"),
        ("https://example.com/paper.pdf", None),
        ("not an id", None),
        ("", None),
    ],
)
def test_arxiv_parse_id_and_can_handle(ref, expected):
    assert A.parse_arxiv_id(ref) == expected
    src = A.ArxivSource()
    assert isinstance(src, IPaperSource)
    assert src.can_handle(ref) is (expected is not None)


def test_arxiv_fetch_rejects_non_arxiv():
    with pytest.raises(A.ArxivError):
        A.ArxivSource().fetch("https://example.com/foo.pdf")


def test_arxiv_search_empty_query_no_network():
    res = A.ArxivSource().search("   ")
    assert res.hits == [] and res.error == "empty query"


def test_arxiv_parse_feed_minimal():
    # a tiny Atom feed shaped like the arXiv API response
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/1706.03762v5</id>
        <updated>2017-12-06T03:30:00Z</updated>
        <published>2017-06-12T17:00:00Z</published>
        <title>Attention Is All You Need</title>
        <summary> We propose the Transformer. </summary>
        <author><name>Ashish Vaswani</name></author>
        <author><name>Noam Shazeer</name></author>
        <arxiv:journal_ref>NeurIPS 2017</arxiv:journal_ref>
        <arxiv:doi>10.5555/3295222.3295349</arxiv:doi>
      </entry>
    </feed>"""
    entries = A._parse_feed(feed)
    assert len(entries) == 1
    e = entries[0]
    assert e["title"] == "Attention Is All You Need"
    assert e["arxiv_id"] == "1706.03762"
    assert e["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert e["year"] == 2017
    assert e["doi"] == "10.5555/3295222.3295349"
    assert e["venue"] == "NeurIPS 2017"
    assert e["abstract"] == "We propose the Transformer."
    assert e["url"] == "https://arxiv.org/abs/1706.03762"


# --------------------------------------------------------------------------- #
# truncate_words
# --------------------------------------------------------------------------- #
def test_truncate_words():
    assert P.truncate_words("") is None
    assert P.truncate_words("short text") == "short text"
    long = " ".join(["word"] * 100)
    out = P.truncate_words(long, max_words=10)
    assert out.startswith("word word") and "truncated" in out and len(out.split()) < 100


# --------------------------------------------------------------------------- #
# PDF extractor — guards
# --------------------------------------------------------------------------- #
def test_extract_pdf_text_guards():
    with pytest.raises(P.PdfError):
        P.extract_pdf_text(b"")
    with pytest.raises(P.PdfError):
        P.extract_pdf_text(b"this is not a pdf at all")
    # over the size limit (>1 MB of leading-%PDF junk; checked before parsing)
    with pytest.raises(P.PdfError):
        P.extract_pdf_text(b"%PDF-" + b"\x00" * (1024 * 1024 * 2))


def test_extract_pdf_text_rejects_textless_pdf():
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    from io import BytesIO

    buf = BytesIO()
    writer.write(buf)
    with pytest.raises(P.PdfError, match="scanned|image-only|characters"):
        P.extract_pdf_text(buf.getvalue(), source_hint="blank.pdf")


# --------------------------------------------------------------------------- #
# PDF extractor — happy path with a hand-built PDF (the `sample_pdf_bytes`
# fixture and `make_simple_pdf` helper live in tests/conftest.py)
# --------------------------------------------------------------------------- #
def test_pdf_source_can_handle(tmp_path, sample_pdf_bytes):
    src = P.PdfSource()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(sample_pdf_bytes)
    assert src.can_handle(str(pdf_path)) is True
    assert src.can_handle("file://" + str(pdf_path)) is True
    assert src.can_handle("https://example.com/x.pdf") is True
    assert src.can_handle(str(tmp_path / "missing.pdf")) is False
    assert src.can_handle("https://example.com/notapdf") is False
    assert src.can_handle("") is False


def test_pdf_source_fetch_local(tmp_path, sample_pdf_bytes):
    pdf_path = tmp_path / "my_great_paper.pdf"
    pdf_path.write_bytes(sample_pdf_bytes)
    fetched = P.PdfSource().fetch(str(pdf_path))
    assert fetched.source == "pdf"
    assert fetched.raw_text and "abstract" in fetched.raw_text.lower()
    assert fetched.raw_text_truncated == fetched.raw_text  # short -> not truncated
    assert fetched.title  # heuristic title or filename-derived
    assert fetched.url and fetched.url.startswith("file://")


def test_pdf_source_fetch_missing_file(tmp_path):
    with pytest.raises(P.PdfError):
        P.PdfSource().fetch(str(tmp_path / "nope.pdf"))
