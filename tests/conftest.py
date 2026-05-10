"""Shared test fixtures: a tiny hand-built PDF (some pypdf builds need a real
PDF for ``extract_text`` to return anything, so we generate one with a content
stream rather than mocking the extractor)."""

from __future__ import annotations

import pytest

from strata_mcp.adapters import pdf_extractor as P


def make_simple_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF whose page draws ``lines`` of text (correct
    xref offsets, so even strict parsers accept it)."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = b"BT /F1 12 Tf 50 760 Td 14 TL\n"
    ops += b"".join(("(" + esc(line) + ") Tj T*\n").encode("latin-1") for line in lines)
    ops += b"ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(ops) + ops + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objs) + 1)
    out += b"startxref\n%d\n%%%%EOF" % xref_pos
    return bytes(out)


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    pytest.importorskip("pypdf")
    lines = [f"This is line {i} of the abstract of a small test paper." for i in range(20)]
    data = make_simple_pdf(lines)
    try:
        P.extract_pdf_text(data, source_hint="sample")
    except P.PdfError:
        pytest.skip("hand-built PDF not extractable by this pypdf version")
    return data
