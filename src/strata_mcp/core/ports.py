"""Ports (interfaces) for the hexagonal architecture.

The domain depends only on these; adapters in ``strata_mcp.adapters`` implement
them. Swapping SQLite for another DB = a new ``IStorage`` impl. Adding a paper
source (PubMed, bioRxiv) = a new ``IPaperSource`` impl.

Scaffold note: signatures are defined; bodies are ``...`` (phase 6/7 fills them).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from strata_mcp.core.entities import (
    Candidate,
    Draft,
    Gap,
    IngestQueueItem,
    LiteratureReview,
    Paper,
    PaperAnalysis,
    PaperProject,
    Project,
    RepoSnapshot,
)


# --------------------------------------------------------------------------- #
# Value objects exchanged with adapters
# --------------------------------------------------------------------------- #
@dataclass
class FetchedPaper:
    """Result of fetching a paper's metadata + text from a source. ``raw_text``
    may be ``None`` (paywalled, scanned PDF, ...). ``raw_text_truncated`` is a
    shortened version (abstract + intro + methods + results + conclusion) sized
    to fit a Haiku subagent's context window for round-1 analysis."""

    title: str
    doi: str | None = None
    arxiv_id: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    url: str | None = None
    abstract: str | None = None
    raw_text: str | None = None
    raw_text_truncated: str | None = None
    source: str = "unknown"


@dataclass
class SearchHit:
    """One result from an external paper search (arXiv / Semantic Scholar)."""

    title: str
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    url: str | None = None
    source: str = "unknown"


@dataclass
class SearchResult:
    """The outcome of one external search query (may have partially failed,
    e.g. rate-limited — ``error`` set, ``hits`` may still hold what was got)."""

    query: str
    hits: list[SearchHit] = field(default_factory=list)
    error: str | None = None


# --------------------------------------------------------------------------- #
# IStorage — the per-project SQLite + FTS5 database
# --------------------------------------------------------------------------- #
class IStorage(ABC):
    """Persistence port. The SQLite adapter owns the schema, migrations and the
    FTS5 index. All paper/gap/draft/candidate reads are scoped by ``project_id``."""

    # --- lifecycle ---------------------------------------------------------- #
    @abstractmethod
    def init(self) -> None:
        """Create ``.strata/`` + the DB if missing, apply migrations, verify
        FTS5 availability and DB integrity (``quick_check``), set PRAGMAs
        (WAL, busy_timeout)."""
        ...

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Diagnostics: schema version, FTS5 ok, integrity ok, row counts,
        queue stats."""
        ...

    @abstractmethod
    def close(self) -> None: ...

    # --- projects & phases -------------------------------------------------- #
    @abstractmethod
    def create_project(self, project: Project) -> Project: ...

    @abstractmethod
    def get_project(self, project_id: str) -> Project | None: ...

    @abstractmethod
    def list_projects(self) -> list[Project]: ...

    @abstractmethod
    def set_phase(
        self, project_id: str, phase_number: int, status: str, notes: str | None = None
    ) -> None: ...

    @abstractmethod
    def get_phases(self, project_id: str) -> list[dict[str, Any]]: ...

    # --- papers & analysis -------------------------------------------------- #
    @abstractmethod
    def save_paper(self, paper: Paper, project_id: str | None = None) -> Paper:
        """Upsert by dedupe key (doi -> arxiv_id-without-version -> normalised
        title). If ``project_id`` given, link via ``paper_project``."""
        ...

    @abstractmethod
    def get_paper(self, paper_id: str, project_id: str | None = None) -> Paper | None: ...

    @abstractmethod
    def list_papers(self, project_id: str) -> list[Paper]: ...

    @abstractmethod
    def find_paper_by_dedupe_key(self, key: str) -> Paper | None: ...

    @abstractmethod
    def save_paper_analysis(self, analysis: PaperAnalysis) -> None: ...

    @abstractmethod
    def get_paper_analysis(self, paper_id: str) -> PaperAnalysis | None: ...

    @abstractmethod
    def save_context(self, link: PaperProject) -> None: ...

    @abstractmethod
    def get_context(self, paper_id: str, project_id: str) -> PaperProject | None: ...

    # --- full-text search (FTS5 BM25, replaces ChromaDB) -------------------- #
    @abstractmethod
    def search(self, project_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Sanitised FTS5 search over papers (title/abstract/raw_text) and
        analyses. Empty/whitespace query -> []."""
        ...

    # --- ingest queue ------------------------------------------------------- #
    @abstractmethod
    def queue_papers(
        self, project_id: str, urls: list[str], hint: str | None = None
    ) -> list[IngestQueueItem]:
        """Enqueue, deduping against already-active (pending/processing) URLs."""
        ...

    @abstractmethod
    def dequeue_paper(self, worker_id: str) -> IngestQueueItem | None:
        """Atomically claim one pending item (BEGIN IMMEDIATE), after first
        resetting stale ``processing`` items to ``pending``. Returns ``None``
        when the queue is empty."""
        ...

    @abstractmethod
    def get_queue_item(self, queue_id: int) -> IngestQueueItem | None:
        """One queue item by id, or ``None``."""
        ...

    @abstractmethod
    def stage_raw_text(self, queue_id: int, text: str | None) -> None:
        """Park a queued paper's full ``raw_text`` in the queue row so it can be
        fetched once server-side and later persisted via ``save_paper`` without
        the caller round-tripping the large text. ``mark_ingested`` clears it."""
        ...

    @abstractmethod
    def get_staged_raw_text(self, queue_id: int) -> str | None:
        """The ``raw_text`` staged by :meth:`stage_raw_text`, or ``None``."""
        ...

    @abstractmethod
    def mark_ingested(self, queue_id: int) -> None: ...

    @abstractmethod
    def mark_failed(
        self, queue_id: int, error: str, raw: str | None = None, permanent: bool = False
    ) -> None:
        """Record the failure. If ``permanent`` (a hard 404 / not-a-paper) or
        ``attempts`` now exceeds the cap, the item is permanently ``failed``
        (it won't be reclaimed by the stale-reset or retried); otherwise it goes
        back to ``pending`` for another attempt."""
        ...

    @abstractmethod
    def queue_status(self, project_id: str) -> dict[str, int]: ...

    # --- gaps / literature reviews / drafts (append-only versions) ---------- #
    @abstractmethod
    def save_gap(self, project_id: str, content_md: str) -> Gap: ...

    @abstractmethod
    def get_gap(self, project_id: str, version: int | None = None) -> Gap | None: ...

    @abstractmethod
    def save_literature_review(self, project_id: str, content_md: str) -> LiteratureReview: ...

    @abstractmethod
    def get_literature_review(
        self, project_id: str, version: int | None = None
    ) -> LiteratureReview | None: ...

    @abstractmethod
    def save_draft(self, project_id: str, content_md: str, section: str | None = None) -> Draft: ...

    @abstractmethod
    def get_latest_draft(self, project_id: str, section: str | None = None) -> Draft | None: ...

    # --- scout candidates --------------------------------------------------- #
    @abstractmethod
    def save_candidates(self, project_id: str, candidates: list[Candidate]) -> list[Candidate]: ...

    @abstractmethod
    def list_candidates(self, project_id: str, status: str | None = None) -> list[Candidate]: ...

    @abstractmethod
    def set_candidate_status(
        self, candidate_id: str, status: str, paper_id: str | None = None
    ) -> None: ...

    # --- repo snapshots ----------------------------------------------------- #
    @abstractmethod
    def save_repo_snapshot(self, snapshot: RepoSnapshot) -> None: ...

    @abstractmethod
    def get_repo_snapshot(self, project_id: str) -> RepoSnapshot | None: ...


# --------------------------------------------------------------------------- #
# IPaperSource — fetch / search papers (no LLM)
# --------------------------------------------------------------------------- #
class IPaperSource(ABC):
    """A source of papers: given a URL/DOI/path, return metadata + text; given a
    query, return search hits. Implementations: arXiv, Semantic Scholar, PDF
    extractor, generic web scraper."""

    name: str = "unknown"

    @abstractmethod
    def can_handle(self, ref: str) -> bool:
        """True if ``ref`` (URL / DOI / arxiv id / file path) belongs to this
        source."""
        ...

    @abstractmethod
    def fetch(self, ref: str) -> FetchedPaper:
        """Fetch metadata + text. Raises a descriptive exception on a hard
        failure (404, scanned PDF, encrypted, not-a-paper)."""
        ...

    def search(self, query: str, max_results: int = 20) -> SearchResult:
        """Optional: external search. Default impl = "not supported"."""
        return SearchResult(query=query, error=f"{self.name} does not support search")


# --------------------------------------------------------------------------- #
# IRepoSource — code repo introspection (no LLM; Claude summarises later)
# --------------------------------------------------------------------------- #
class IRepoSource(ABC):
    """Introspects a project's code repo into layers (readme/tree/pending,
    agents, benchmarks/metrics, datasets/configs). Returns raw structured data;
    Claude Code summarises it into the gap/draft prompt."""

    @abstractmethod
    def scan(self, repo_url_or_path: str, project_id: str) -> RepoSnapshot:
        """Returns a ``RepoSnapshot`` for ``project_id``. On an inaccessible /
        private repo without a token, degrades to a snapshot whose ``layers``
        carries an ``error`` note rather than raising."""
        ...
