"""SQLite + FTS5 implementation of :class:`IStorage`.

One database per research-project directory: ``./.strata/strata.db`` (relative
to the directory Claude Code runs in), mirroring ``.apex/context.db``. A single
DB can hold several ``projects`` (a research line and the papers derived from
it, sharing a library).

Hardening (see EDGE-CASES + decision #11):
  * ``PRAGMA journal_mode=WAL`` + ``PRAGMA busy_timeout`` — concurrent readers
    (another session, strata-hub) plus one writer, no instant "database is
    locked".
  * ``PRAGMA quick_check`` on open; FTS5 availability probed on init.
  * ``papers_fts`` / ``analyses_fts`` kept in sync by AFTER triggers.
  * ``strata_search`` sanitises the FTS5 query (quote each token).
  * ``raw_text`` is sanitised before storage (no NUL/control chars, NFKC).
  * append-only ``version`` for gaps/lit-reviews/drafts, computed inside
    ``BEGIN IMMEDIATE``.
  * atomic ``dequeue_paper`` with stale-``processing`` reclaim.

Scaffold note: the schema is defined; method bodies land in phase 7.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
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
from strata_mcp.core.ports import IStorage

SCHEMA_VERSION = 1

DEFAULT_DB_PATH = ".strata/strata.db"
STALE_QUEUE_MINUTES_DEFAULT = 15
MAX_INGEST_ATTEMPTS_DEFAULT = 3

# Applied in order on open; each migration is idempotent (IF NOT EXISTS / guarded).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS project_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT,
    research_question TEXT,
    template          TEXT NOT NULL DEFAULT 'generic',
    repo_url          TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phases (
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    phase_number INTEGER NOT NULL,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    started_at   TEXT,
    completed_at TEXT,
    notes        TEXT,
    PRIMARY KEY (project_id, phase_number)
);

CREATE TABLE IF NOT EXISTS papers (
    id           TEXT PRIMARY KEY,
    dedupe_key   TEXT UNIQUE,
    doi          TEXT,
    arxiv_id     TEXT,
    title        TEXT NOT NULL,
    authors_json TEXT,
    year         INTEGER,
    venue        TEXT,
    url          TEXT,
    abstract     TEXT,
    raw_text     TEXT,
    source       TEXT NOT NULL DEFAULT 'unknown',
    ingested_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_arxiv ON papers(arxiv_id);

CREATE TABLE IF NOT EXISTS paper_analyses (
    paper_id    TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    round1_json TEXT,
    round2_json TEXT,
    model_used  TEXT,
    analyzed_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_project (
    paper_id              TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    project_id            TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    context_analysis_json TEXT,
    relevance_score       REAL,
    added_at              TEXT NOT NULL,
    PRIMARY KEY (paper_id, project_id)
);

CREATE TABLE IF NOT EXISTS ingest_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,
    url_normalized TEXT,
    hint           TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    worker_id      TEXT,
    error          TEXT,
    raw            TEXT,
    queued_at      TEXT NOT NULL,
    processed_at   TEXT
);
-- one active (pending/processing) enqueue per (project, url)
CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_active
    ON ingest_queue(project_id, url_normalized)
    WHERE status IN ('pending', 'processing');
CREATE INDEX IF NOT EXISTS idx_queue_status ON ingest_queue(status, id);

CREATE TABLE IF NOT EXISTS gap_analyses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    content_md TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS literature_reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    content_md TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS drafts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    section    TEXT,
    content_md TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, version, section)
);

CREATE TABLE IF NOT EXISTS candidates (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    abstract         TEXT,
    authors_json     TEXT,
    arxiv_id         TEXT,
    doi              TEXT,
    url              TEXT,
    relevance_score  REAL,
    relevance_reason TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    paper_id         TEXT REFERENCES papers(id) ON DELETE SET NULL,
    source           TEXT NOT NULL DEFAULT 'unknown',
    scouted_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_project ON candidates(project_id, status);

CREATE TABLE IF NOT EXISTS repo_snapshots (
    project_id  TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    repo_url    TEXT NOT NULL,
    layers_json TEXT,
    scanned_at  TEXT NOT NULL
);

-- FTS5 (replaces ChromaDB): BM25 over paper text and analyses.
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, raw_text,
    content='papers', content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2"
);
CREATE VIRTUAL TABLE IF NOT EXISTS analyses_fts USING fts5(
    round1_text, round2_text,
    content='paper_analyses', content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2"
);

-- Triggers keep the FTS indexes in sync with their content tables.
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, raw_text)
    VALUES (new.rowid, new.title, new.abstract, new.raw_text);
END;
CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, raw_text)
    VALUES ('delete', old.rowid, old.title, old.abstract, old.raw_text);
END;
CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, raw_text)
    VALUES ('delete', old.rowid, old.title, old.abstract, old.raw_text);
    INSERT INTO papers_fts(rowid, title, abstract, raw_text)
    VALUES (new.rowid, new.title, new.abstract, new.raw_text);
END;
"""


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class SqliteStorage(IStorage):
    """SQLite-backed storage. Owns the schema, migrations and the FTS5 index."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # --- lifecycle --------------------------------------------------------- #
    def init(self) -> None:  # noqa: D401
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- projects & phases ------------------------------------------------- #
    def create_project(self, project: Project) -> Project:
        raise NotImplementedError

    def get_project(self, project_id: str) -> Project | None:
        raise NotImplementedError

    def list_projects(self) -> list[Project]:
        raise NotImplementedError

    def set_phase(
        self, project_id: str, phase_number: int, status: str, notes: str | None = None
    ) -> None:
        raise NotImplementedError

    def get_phases(self, project_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    # --- papers & analysis ------------------------------------------------- #
    def save_paper(self, paper: Paper, project_id: str | None = None) -> Paper:
        raise NotImplementedError

    def get_paper(self, paper_id: str, project_id: str | None = None) -> Paper | None:
        raise NotImplementedError

    def list_papers(self, project_id: str) -> list[Paper]:
        raise NotImplementedError

    def find_paper_by_dedupe_key(self, key: str) -> Paper | None:
        raise NotImplementedError

    def save_paper_analysis(self, analysis: PaperAnalysis) -> None:
        raise NotImplementedError

    def get_paper_analysis(self, paper_id: str) -> PaperAnalysis | None:
        raise NotImplementedError

    def save_context(self, link: PaperProject) -> None:
        raise NotImplementedError

    def get_context(self, paper_id: str, project_id: str) -> PaperProject | None:
        raise NotImplementedError

    # --- search ------------------------------------------------------------ #
    def search(self, project_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    # --- ingest queue ------------------------------------------------------ #
    def queue_papers(
        self, project_id: str, urls: list[str], hint: str | None = None
    ) -> list[IngestQueueItem]:
        raise NotImplementedError

    def dequeue_paper(self, worker_id: str) -> IngestQueueItem | None:
        raise NotImplementedError

    def mark_ingested(self, queue_id: int) -> None:
        raise NotImplementedError

    def mark_failed(self, queue_id: int, error: str, raw: str | None = None) -> None:
        raise NotImplementedError

    def queue_status(self, project_id: str) -> dict[str, int]:
        raise NotImplementedError

    # --- gaps / lit reviews / drafts --------------------------------------- #
    def save_gap(self, project_id: str, content_md: str) -> Gap:
        raise NotImplementedError

    def get_gap(self, project_id: str, version: int | None = None) -> Gap | None:
        raise NotImplementedError

    def save_literature_review(self, project_id: str, content_md: str) -> LiteratureReview:
        raise NotImplementedError

    def get_literature_review(
        self, project_id: str, version: int | None = None
    ) -> LiteratureReview | None:
        raise NotImplementedError

    def save_draft(self, project_id: str, content_md: str, section: str | None = None) -> Draft:
        raise NotImplementedError

    def get_latest_draft(self, project_id: str, section: str | None = None) -> Draft | None:
        raise NotImplementedError

    # --- candidates -------------------------------------------------------- #
    def save_candidates(self, project_id: str, candidates: list[Candidate]) -> list[Candidate]:
        raise NotImplementedError

    def list_candidates(self, project_id: str, status: str | None = None) -> list[Candidate]:
        raise NotImplementedError

    def set_candidate_status(
        self, candidate_id: str, status: str, paper_id: str | None = None
    ) -> None:
        raise NotImplementedError

    # --- repo snapshots ---------------------------------------------------- #
    def save_repo_snapshot(self, snapshot: RepoSnapshot) -> None:
        raise NotImplementedError

    def get_repo_snapshot(self, project_id: str) -> RepoSnapshot | None:
        raise NotImplementedError
