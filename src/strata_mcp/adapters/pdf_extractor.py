"""PDF paper source: extract text from a local file path or a downloaded PDF.

Defensive: checks the path exists and is a file, the ``%PDF-`` magic bytes, the
size against ``STRATA_MAX_PDF_MB``, and that enough text came out (a scanned /
image-only PDF yields almost nothing -> hard failure with a clear reason; no OCR
attempted). Encrypted PDFs -> hard failure. Extracted text is Unicode-normalised
(NFKC) and stripped of control characters by the storage layer.

Scaffold note: signatures only; implementation in phase 7.
"""

from __future__ import annotations

from strata_mcp.core.ports import FetchedPaper, IPaperSource

PDF_MAGIC = b"%PDF-"
MIN_EXTRACTED_CHARS = 500  # below this we treat the PDF as scanned / unusable


class PdfSource(IPaperSource):
    name = "pdf"

    def can_handle(self, ref: str) -> bool:
        raise NotImplementedError

    def fetch(self, ref: str) -> FetchedPaper:
        raise NotImplementedError
