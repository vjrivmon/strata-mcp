"""Domain entities for strata-mcp.

Plain dataclasses mirroring the ``.strata/strata.db`` schema (see the data-model
in the project wiki). No persistence logic here — adapters map these to/from
SQLite. JSON-shaped fields (analysis rounds, context analysis, repo layers) are
kept as ``dict`` and serialised by the storage adapter.

Scaffold note: fields are defined; behaviour (validation, factories) lands in
phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


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


def _now() -> str:
    """ISO-8601 UTC timestamp used for ``*_at`` columns."""
    return datetime.now(timezone.utc).isoformat()


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


@dataclass
class Round1Analysis:
    """Quick triage of a paper (skill ``analyze-paper``, round 1)."""

    bullets: list[str] = field(default_factory=list)
    relevance_score: float | None = None  # 0-10
    worth_reading: bool | None = None
    summary_es: str | None = None


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


@dataclass
class PaperProject:
    """N:N link between a paper and a project, with project-specific analysis."""

    paper_id: str
    project_id: str
    context_analysis: dict[str, Any] = field(default_factory=dict)
    # shape: {contribution_to_project, gaps_covered[], gaps_not_covered[]}
    relevance_score: float | None = None  # 0-10, in this project's context
    added_at: str = field(default_factory=_now)


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
    section: str | None = None  # intro|related_work|methodology|results|...
    created_at: str = field(default_factory=_now)


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
