"""Domain entities for strata-mcp.

Dataclasses mirroring the ``.strata/strata.db`` schema (see the data-model in
the project wiki). No persistence logic here — adapters map these to/from
SQLite. JSON-shaped fields (analysis rounds, context analysis, repo layers) are
kept as ``dict`` and serialised by the storage adapter.

What *does* live here, beyond the fields:

* light ``__post_init__`` validation — enum-string coercion (so a row loaded
  from SQLite, where ``source`` is the text ``"arxiv"``, becomes the enum) and
  range checks on the ``*_score`` fields (0-10);
* ``create()`` factory classmethods on the entities that own a public id
  (``Project``, ``Paper``, ``Candidate``) — they mint the id, normalise the
  author list and reject empty/unknown values, so callers never hand-roll one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# Paper templates the export phase knows how to render.
VALID_TEMPLATES: tuple[str, ...] = ("lncs", "ieee", "acm", "inted", "generic")

# Sections a draft may be scoped to (``None`` = the whole paper).
VALID_DRAFT_SECTIONS: tuple[str, ...] = (
    "abstract",
    "introduction",
    "related_work",
    "methodology",
    "results",
    "discussion",
    "conclusion",
)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class PaperSource(str, Enum):
    """Where a paper's metadata/text came from."""

    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    DOI = "doi"
    PDF = "pdf"
    WEB = "web"
    UNKNOWN = "unknown"


class IngestStatus(str, Enum):
    """Lifecycle of an ``ingest_queue`` row."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class CandidateStatus(str, Enum):
    """Lifecycle of a scout ``candidate``."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ALREADY_IN_LIBRARY = "already_in_library"


class ResearchPhase(int, Enum):
    """Phases of the ``/strata`` workflow (mirrors the apex phase pattern)."""

    SETUP = 0
    LIBRARY = 1
    RELEVANCE = 2
    GAP = 3
    SCOUT = 4
    LITERATURE_REVIEW = 5
    DRAFT = 6
    QA = 7
    EXPORT = 8
    ITERATION = 9


# --------------------------------------------------------------------------- #
# Small validators / coercers shared by entities and the storage adapter
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    """ISO-8601 UTC timestamp used for ``*_at`` columns."""
    return datetime.now(timezone.utc).isoformat()


# Backwards-friendly alias used by the default_factory below.
_now = now_iso


def new_id() -> str:
    """A fresh opaque identifier for a ``Project`` / ``Paper`` / ``Candidate``
    (32-char hex; collision-free for any realistic library size)."""
    return uuid.uuid4().hex


def coerce_score(value: object, *, field_name: str = "score") -> float | None:
    """Return ``value`` as a float in ``[0, 10]``; ``None`` passes through.

    Raises :class:`ValueError` for a non-numeric or out-of-range value — scores
    are always produced by us (relevance analyses, scout ranking), so a bad one
    is a bug worth surfacing rather than silently clamping.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number, got {value!r}") from exc
    if v != v or not (0.0 <= v <= 10.0):  # v != v catches NaN
        raise ValueError(f"{field_name} must be within [0, 10], got {v}")
    return v


def coerce_year(value: object) -> int | None:
    """Return ``value`` as a plausible publication year, or ``None``."""
    if value is None:
        return None
    try:
        y = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return y if 1500 <= y <= 2200 else None


def normalize_authors(authors: Iterable[str] | str | None) -> list[str]:
    """Trim, drop empties and de-duplicate (order-preserving) an author list.

    Accepts a single string (treated as one author), an iterable of strings, or
    ``None``.
    """
    if authors is None:
        return []
    if isinstance(authors, str):
        authors = [authors]
    seen: set[str] = set()
    out: list[str] = []
    for a in authors:
        if a is None:
            continue
        name = str(a).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


def _require_nonblank(value: str | None, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required and cannot be blank")
    return text


def _coerce_template(value: str | None) -> str:
    t = (value or "generic").strip().lower()
    if t not in VALID_TEMPLATES:
        raise ValueError(f"unknown template {t!r}; valid: {', '.join(VALID_TEMPLATES)}")
    return t


def _coerce_section(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip().lower()
    if not s:
        return None
    if s not in VALID_DRAFT_SECTIONS:
        raise ValueError(f"unknown draft section {s!r}; valid: {', '.join(VALID_DRAFT_SECTIONS)}")
    return s


# --------------------------------------------------------------------------- #
# Core entities
# --------------------------------------------------------------------------- #
@dataclass
class Project:
    """A research line: a thesis/paper with its own library of papers."""

    id: str
    name: str
    description: str | None = None
    research_question: str | None = None
    template: str = "generic"  # lncs | ieee | acm | inted | generic
    repo_url: str | None = None
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.id = _require_nonblank(self.id, "project id")
        self.name = _require_nonblank(self.name, "project name")
        self.template = _coerce_template(self.template)

    @classmethod
    def create(
        cls,
        name: str,
        *,
        description: str | None = None,
        research_question: str | None = None,
        template: str = "generic",
        repo_url: str | None = None,
    ) -> Project:
        return cls(
            id=new_id(),
            name=name,
            description=description,
            research_question=research_question,
            template=template,
            repo_url=repo_url,
        )


@dataclass
class Phase:
    """State of one phase of the research workflow for a project."""

    project_id: str
    phase_number: int
    name: str
    status: str = "pending"  # pending | in_progress | completed | skipped
    started_at: str | None = None
    completed_at: str | None = None
    notes: str | None = None


@dataclass
class Paper:
    """A paper in the library. ``raw_text`` may be ``None`` (paywalled, queued
    but not yet fetched, scanned PDF, ...)."""

    id: str
    title: str
    doi: str | None = None
    arxiv_id: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    url: str | None = None
    abstract: str | None = None
    raw_text: str | None = None
    source: PaperSource = PaperSource.UNKNOWN
    ingested_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.id = _require_nonblank(self.id, "paper id")
        self.title = _require_nonblank(self.title, "paper title")
        self.authors = normalize_authors(self.authors)
        self.year = coerce_year(self.year)
        self.source = PaperSource(self.source)

    @classmethod
    def create(
        cls,
        title: str,
        *,
        doi: str | None = None,
        arxiv_id: str | None = None,
        authors: Iterable[str] | str | None = None,
        year: object = None,
        venue: str | None = None,
        url: str | None = None,
        abstract: str | None = None,
        raw_text: str | None = None,
        source: PaperSource | str = PaperSource.UNKNOWN,
    ) -> Paper:
        return cls(
            id=new_id(),
            title=title,
            doi=doi,
            arxiv_id=arxiv_id,
            authors=normalize_authors(authors),
            year=coerce_year(year),
            venue=venue,
            url=url,
            abstract=abstract,
            raw_text=raw_text,
            source=PaperSource(source),
        )


@dataclass
class Round1Analysis:
    """Quick triage of a paper (skill ``analyze-paper``, round 1)."""

    bullets: list[str] = field(default_factory=list)
    relevance_score: float | None = None  # 0-10
    worth_reading: bool | None = None
    summary_es: str | None = None

    def __post_init__(self) -> None:
        self.bullets = [
            str(b).strip() for b in (self.bullets or []) if b is not None and str(b).strip()
        ]
        self.relevance_score = coerce_score(self.relevance_score, field_name="relevance_score")


@dataclass
class Round2Analysis:
    """Deep analysis of a paper (skill ``analyze-paper``, round 2)."""

    intro_summary: str | None = None
    related_work: str | None = None
    methodology: str | None = None
    results: str | None = None
    strengths: str | None = None
    limitations: str | None = None
    key_contributions: str | None = None


@dataclass
class PaperAnalysis:
    """The two-round analysis of a paper. Either round may be ``None`` (round 1
    saved, round 2 still pending or failed). ``model_used`` tracks which model
    produced it (e.g. ``haiku``)."""

    paper_id: str
    round1: Round1Analysis | None = None
    round2: Round2Analysis | None = None
    model_used: str | None = None
    analyzed_at: str | None = None

    def __post_init__(self) -> None:
        self.paper_id = _require_nonblank(self.paper_id, "paper_id")


@dataclass
class PaperProject:
    """N:N link between a paper and a project, with project-specific analysis."""

    paper_id: str
    project_id: str
    context_analysis: dict[str, Any] = field(default_factory=dict)
    # shape: {contribution_to_project, gaps_covered[], gaps_not_covered[]}
    relevance_score: float | None = None  # 0-10, in this project's context
    added_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.paper_id = _require_nonblank(self.paper_id, "paper_id")
        self.project_id = _require_nonblank(self.project_id, "project_id")
        self.relevance_score = coerce_score(self.relevance_score, field_name="relevance_score")


@dataclass
class IngestQueueItem:
    """A paper queued for ingestion. Drained by Haiku subagents launched from
    ``/strata``. ``attempts`` caps retries; ``processed_at`` lets a stale
    ``processing`` row (dead worker) be reclaimed."""

    id: int | None  # autoincrement; None before insert
    project_id: str
    url: str
    hint: str | None = None  # caller-provided type hint: arxiv|doi|pdf|web
    status: IngestStatus = IngestStatus.PENDING
    attempts: int = 0
    worker_id: str | None = None
    error: str | None = None
    queued_at: str = field(default_factory=_now)
    processed_at: str | None = None

    def __post_init__(self) -> None:
        self.project_id = _require_nonblank(self.project_id, "project_id")
        self.url = _require_nonblank(self.url, "url")
        self.status = IngestStatus(self.status)
        self.attempts = max(0, int(self.attempts))


@dataclass
class Gap:
    """A gap analysis for a project. Versions are append-only (never overwritten)."""

    id: int | None
    project_id: str
    version: int
    content_md: str
    created_at: str = field(default_factory=_now)


@dataclass
class LiteratureReview:
    """A related-work / state-of-the-art write-up. Append-only versions."""

    id: int | None
    project_id: str
    version: int
    content_md: str
    created_at: str = field(default_factory=_now)


@dataclass
class Draft:
    """A paper draft (or one section of it). ``section`` is ``None`` for a full
    draft. Append-only versions."""

    id: int | None
    project_id: str
    version: int
    content_md: str
    section: str | None = None  # abstract|introduction|related_work|...
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.section = _coerce_section(self.section)


@dataclass
class Candidate:
    """A paper found by the scout, awaiting approve/reject. On approval it is
    re-queued into the ingest queue and ``paper_id`` is set."""

    id: str
    project_id: str
    title: str
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    arxiv_id: str | None = None
    doi: str | None = None
    url: str | None = None
    relevance_score: float | None = None  # 0-10
    relevance_reason: str | None = None
    status: CandidateStatus = CandidateStatus.PENDING
    paper_id: str | None = None
    source: PaperSource = PaperSource.UNKNOWN
    scouted_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.id = _require_nonblank(self.id, "candidate id")
        self.project_id = _require_nonblank(self.project_id, "project_id")
        self.title = _require_nonblank(self.title, "candidate title")
        self.authors = normalize_authors(self.authors)
        self.relevance_score = coerce_score(self.relevance_score, field_name="relevance_score")
        self.status = CandidateStatus(self.status)
        self.source = PaperSource(self.source)

    @classmethod
    def create(
        cls,
        project_id: str,
        title: str,
        *,
        abstract: str | None = None,
        authors: Iterable[str] | str | None = None,
        arxiv_id: str | None = None,
        doi: str | None = None,
        url: str | None = None,
        relevance_score: object = None,
        relevance_reason: str | None = None,
        source: PaperSource | str = PaperSource.UNKNOWN,
    ) -> Candidate:
        return cls(
            id=new_id(),
            project_id=project_id,
            title=title,
            abstract=abstract,
            authors=normalize_authors(authors),
            arxiv_id=arxiv_id,
            doi=doi,
            url=url,
            relevance_score=coerce_score(relevance_score, field_name="relevance_score"),
            relevance_reason=relevance_reason,
            source=PaperSource(source),
        )


@dataclass
class RepoSnapshot:
    """Layered introspection of a project's code repo (skill ``gap-analysis``
    uses it for the "your proposed solution" section). One per project."""

    project_id: str
    repo_url: str
    layers: dict[str, Any] = field(default_factory=dict)
    # shape: {layer1:{readme,tree,pending}, layer2:{agents[]},
    #         layer3:{benchmarks[],metrics}, layer4:{datasets[],configs}}
    scanned_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.project_id = _require_nonblank(self.project_id, "project_id")
        self.repo_url = _require_nonblank(self.repo_url, "repo_url")
