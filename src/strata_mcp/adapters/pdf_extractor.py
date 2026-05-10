"""PDF paper source: extract text from a local file path or a downloaded PDF.

Defensive: checks the path exists and is a file, the ``%PDF-`` magic bytes, the
size against ``STRATA_MAX_PDF_MB`` (default 50 MB), that the file is not
encrypted, and that enough text actually came out — a scanned / image-only PDF
yields almost nothing, which is a hard failure with a clear reason (no OCR is
attempted). Extracted text is left as-is here; the storage layer NFKC-normalises
it and strips control characters.

``extract_pdf_text`` is also reused by the arXiv adapter (which downloads the
PDF and hands the bytes here), so the extraction logic lives in one place.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from strata_mcp.core.ports import FetchedPaper, IPaperSource

PDF_MAGIC = b"%PDF-"
MIN_EXTRACTED_CHARS = 500  # below this we treat the PDF as scanned / unusable
DEFAULT_MAX_PDF_MB = 50


class PdfError(RuntimeError):
    """A PDF could not be turned into usable text (missing, not a PDF, too big,
    encrypted, scanned, corrupt)."""


def _max_pdf_bytes() -> int:
    try:
        mb = float(os.environ.get("STRATA_MAX_PDF_MB", DEFAULT_MAX_PDF_MB))
    except ValueError:
        mb = DEFAULT_MAX_PDF_MB
    return int(max(1.0, mb) * 1024 * 1024)


def extract_pdf_text(data: bytes, *, source_hint: str = "PDF") -> str:
    """Extract text from PDF bytes. Raises :class:`PdfError` on a hard failure
    (not a PDF, encrypted, corrupt, or essentially text-free / scanned)."""
    if not data:
        raise PdfError(f"{source_hint}: empty file")
    if not data[:1024].lstrip()[: len(PDF_MAGIC)] == PDF_MAGIC:
        raise PdfError(f"{source_hint}: not a PDF (missing %PDF- header)")
    if len(data) > _max_pdf_bytes():
        raise PdfError(
            f"{source_hint}: PDF is {len(data) // (1024 * 1024)} MB, over the "
            f"STRATA_MAX_PDF_MB limit ({_max_pdf_bytes() // (1024 * 1024)} MB)"
        )

    try:
        from io import BytesIO

        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - pypdf is a hard dependency
        raise PdfError("pypdf is not installed") from exc

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # try the empty password (common for "owner-only" restrictions)
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - any failure here means we can't read it
                pass
            if reader.is_encrypted:
                raise PdfError(f"{source_hint}: PDF is encrypted")
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - a bad page shouldn't sink the whole document
                continue
    except PdfError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of exception types
        raise PdfError(f"{source_hint}: could not parse PDF ({exc})") from exc

    text = "\n\n".join(p.strip() for p in parts if p and p.strip()).strip()
    if len(text) < MIN_EXTRACTED_CHARS:
        raise PdfError(
            f"{source_hint}: only {len(text)} characters of text extracted — "
            "the PDF is likely scanned / image-only (no OCR is attempted)"
        )
    return text


def _looks_like_pdf_ref(ref: str) -> bool:
    ref = (ref or "").strip()
    if not ref:
        return False
    parts = urlsplit(ref)
    if parts.scheme in ("http", "https"):
        return parts.path.lower().endswith(".pdf")
    if parts.scheme in ("file", ""):
        path = parts.path if parts.scheme == "file" else ref
        return path.lower().endswith(".pdf")
    return False


class PdfSource(IPaperSource):
    """A local ``*.pdf`` file (or a direct ``*.pdf`` URL — downloaded first)."""

    name = "pdf"

    def can_handle(self, ref: str) -> bool:
        if not _looks_like_pdf_ref(ref):
            return False
        parts = urlsplit((ref or "").strip())
        if parts.scheme in ("http", "https"):
            return True
        path = Path(parts.path if parts.scheme == "file" else ref.strip())
        return path.is_file()

    def fetch(self, ref: str) -> FetchedPaper:
        ref = (ref or "").strip()
        parts = urlsplit(ref)
        if parts.scheme in ("http", "https"):
            data = _download(ref)
            url: str | None = ref
            name = parts.path.rsplit("/", 1)[-1] or ref
        else:
            path = Path(parts.path if parts.scheme == "file" else ref)
            if not path.is_file():
                raise PdfError(f"no such file: {path}")
            if path.stat().st_size > _max_pdf_bytes():
                raise PdfError(f"{path.name}: PDF over the STRATA_MAX_PDF_MB limit")
            data = path.read_bytes()
            try:
                url = path.resolve().as_uri()
            except ValueError:
                url = None
            name = path.name

        text = extract_pdf_text(data, source_hint=name)
        title = _guess_title_from_text(text) or _title_from_filename(name)
        return FetchedPaper(
            title=title,
            url=url,
            raw_text=text,
            raw_text_truncated=truncate_words(text),
            source=self.name,
        )


def _download(url: str, *, timeout: float = 30.0) -> bytes:
    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url, headers={"User-Agent": "strata-mcp/0.1"})
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        raise PdfError(f"could not download {url}: {exc}") from exc


def _title_from_filename(name: str) -> str:
    stem = Path(name).stem.replace("_", " ").replace("-", " ").strip()
    return stem or "Untitled PDF"


def _guess_title_from_text(text: str) -> str | None:
    """Heuristic: the first non-trivial line of the first page, if it looks like
    a title (a handful of words, not an URL / arXiv stamp)."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(("arxiv:", "http://", "https://", "doi:")):
            continue
        words = line.split()
        if 2 <= len(words) <= 30 and len(line) <= 250:
            return line
        break
    return None


def truncate_words(text: str, *, max_words: int = 38000) -> str | None:
    """A version of ``text`` short enough to fit a subagent's context window for
    round-1 analysis. Whole text if it's already short enough; otherwise the
    first ``max_words`` words plus a marker."""
    if not text:
        return None
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n\n[... text truncated for length ...]"
