"""SQLite + FTS5 implementation of :class:`IStorage`.

One database per research-project directory: ``./.strata/strata.db`` (relative
to the directory Claude Code runs in), mirroring ``.apex/context.db``. A single
DB can hold several ``projects`` (a research line and the papers derived from
it, sharing a library).

Hardening (EDGE-CASES + decisions #10/#11):

* ``PRAGMA journal_mode=WAL`` + ``PRAGMA busy_timeout`` — concurrent readers
  (another session, strata-hub) plus one writer, no instant "database is
  locked"; ``PRAGMA foreign_keys=ON`` so the CASCADE/SET NULL rules bite.
* ``PRAGMA quick_check`` on open; FTS5 availability probed before use — both
  surface as a clear error rather than erratic behaviour.
* the FTS5 indexes (``papers_fts`` / ``analyses_fts``) are plain (not external
  content) and rebuilt per row on every ``save_paper`` / ``save_paper_analysis``
  — robust and trivially correct for a library of a few hundred papers.
* ``search`` turns the query into quoted ``\\w+`` tokens, so FTS5's operators
  (``AND``/``OR``/``NEAR``/``"``/``*``/``-``) can never leak in; empty -> ``[]``.
* ``raw_text`` / abstracts are NFKC-normalised and stripped of NUL & control
  characters before storage.
* gaps / literature reviews / drafts get a monotone ``version`` computed inside
  ``BEGIN IMMEDIATE``.
* ``dequeue_paper`` claims one item atomically (``BEGIN IMMEDIATE``) after
  reclaiming stale ``processing`` rows; ``attempts`` is bumped on claim and
  capped, so a poison item ends up permanently ``failed`` instead of looping —
  and ``mark_failed(..., permanent=True)`` fails it at once for a hard 404 /
  not-a-paper that no retry can fix.
* ``stage_raw_text`` parks a fetched paper's full text on its queue row so a
  subagent fetches it once (server-side) and persists it via ``save_paper``
  without round-tripping the large text; ``mark_ingested`` clears the staged copy.
* every paper/gap/draft/candidate read is scoped by ``project_id``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from strata_mcp.core import phases as phase_catalogue
from strata_mcp.core.dedupe import (
    dedupe_key_for,
    normalize_arxiv_id,
    normalize_doi,
    normalize_url,
)
from strata_mcp.core.entities import (
    Candidate,
    CandidateStatus,
    Draft,
    Gap,
    IngestQueueItem,
    IngestStatus,
    LiteratureReview,
    Paper,
    PaperAnalysis,
    PaperProject,
    PaperSource,
    Project,
    RepoSnapshot,
    Round1Analysis,
    Round2Analysis,
    now_iso,
)
from strata_mcp.core.ports import IStorage

SCHEMA_VERSION = 1

DEFAULT_DB_PATH = ".strata/strata.db"
STALE_QUEUE_MINUTES_DEFAULT = 15
MAX_INGEST_ATTEMPTS_DEFAULT = 3
BUSY_TIMEOUT_MS_DEFAULT = 5000

_WS = re.compile(r"[ \t]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # keep \t \n \r
_WORD = re.compile(r"\w+", re.UNICODE)

# Every statement is `IF NOT EXISTS` / guarded, so this is safe to run on open.
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
-- at most one *active* (pending/processing) enqueue per (project, url)
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
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_drafts_version
    ON drafts(project_id, version, IFNULL(section, ''));

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

-- FTS5 (replaces ChromaDB): BM25 over paper text and over the analyses.
-- Plain tables, kept in sync by the storage code (no external content / triggers).
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    paper_id UNINDEXED, title, abstract, raw_text,
    tokenize="unicode61 remove_diacritics 2"
);
CREATE VIRTUAL TABLE IF NOT EXISTS analyses_fts USING fts5(
    paper_id UNINDEXED, round1_text, round2_text,
    tokenize="unicode61 remove_diacritics 2"
);
"""


# --------------------------------------------------------------------------- #
# Text & JSON helpers
# --------------------------------------------------------------------------- #
def sanitize_text(text: str | None) -> str | None:
    """NFKC-normalise, drop NUL & control characters, collapse runs of spaces
    and 3+ blank lines. ``None`` / empty -> ``None``."""
    if not text:
        return None
    t = unicodedata.normalize("NFKC", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    t = _CONTROL.sub("", t)
    t = "\n".join(_WS.sub(" ", line).rstrip() for line in t.split("\n"))
    t = _MANY_NEWLINES.sub("\n\n", t).strip()
    return t or None


def _dumps(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _fts_match_expr(query: str) -> str:
    """Turn a free-text query into a safe FTS5 MATCH expression: each ``\\w+``
    run becomes a quoted phrase, ``OR``-ed with the rest, so a multi-word query
    surfaces anything related and bm25 does the ranking (a strict ``AND`` would
    miss a paper that only matches three of four terms). Empty -> ``""``."""
    tokens = _WORD.findall(query or "")
    return " OR ".join(f'"{t}"' for t in tokens)


def _round1_fts_text(r1: Round1Analysis | None) -> str:
    if not r1:
        return ""
    return " ".join(filter(None, [*r1.bullets, r1.summary_es or ""])).strip()


def _round2_fts_text(r2: Round2Analysis | None) -> str:
    if not r2:
        return ""
    return " ".join(
        filter(
            None,
            [
                r2.intro_summary or "",
                r2.related_work or "",
                r2.methodology or "",
                r2.results or "",
                r2.strengths or "",
                r2.limitations or "",
                r2.key_contributions or "",
            ],
        )
    ).strip()


# --------------------------------------------------------------------------- #
# Row mappers
# --------------------------------------------------------------------------- #
def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        research_question=row["research_question"],
        template=row["template"],
        repo_url=row["repo_url"],
        created_at=row["created_at"],
    )


def _row_to_paper(row: sqlite3.Row) -> Paper:
    return Paper(
        id=row["id"],
        title=row["title"],
        doi=row["doi"],
        arxiv_id=row["arxiv_id"],
        authors=_loads(row["authors_json"]) or [],
        year=row["year"],
        venue=row["venue"],
        url=row["url"],
        abstract=row["abstract"],
        raw_text=row["raw_text"],
        source=PaperSource(row["source"]),
        ingested_at=row["ingested_at"],
    )


def _row_to_queue_item(row: sqlite3.Row) -> IngestQueueItem:
    return IngestQueueItem(
        id=row["id"],
        project_id=row["project_id"],
        url=row["url"],
        hint=row["hint"],
        status=IngestStatus(row["status"]),
        attempts=row["attempts"],
        worker_id=row["worker_id"],
        error=row["error"],
        queued_at=row["queued_at"],
        processed_at=row["processed_at"],
    )


def _row_to_candidate(row: sqlite3.Row) -> Candidate:
    return Candidate(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        abstract=row["abstract"],
        authors=_loads(row["authors_json"]) or [],
        arxiv_id=row["arxiv_id"],
        doi=row["doi"],
        url=row["url"],
        relevance_score=row["relevance_score"],
        relevance_reason=row["relevance_reason"],
        status=CandidateStatus(row["status"]),
        paper_id=row["paper_id"],
        source=PaperSource(row["source"]),
        scouted_at=row["scouted_at"],
    )


def _coalesce(new: Any, old: Any) -> Any:
    return new if new not in (None, "", [], {}) else old


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
class SqliteStorage(IStorage):
    """SQLite-backed storage. Owns the schema, migrations and the FTS5 indexes."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        stale_queue_minutes: int = STALE_QUEUE_MINUTES_DEFAULT,
        max_ingest_attempts: int = MAX_INGEST_ATTEMPTS_DEFAULT,
        busy_timeout_ms: int = BUSY_TIMEOUT_MS_DEFAULT,
    ) -> None:
        self.db_path = Path(db_path)
        self.stale_queue_minutes = max(1, int(stale_queue_minutes))
        self.max_ingest_attempts = max(1, int(max_ingest_attempts))
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._connection: sqlite3.Connection | None = None

    # --- internals --------------------------------------------------------- #
    @property
    def _is_memory(self) -> bool:
        return str(self.db_path) == ":memory:"

    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("storage not initialised; call init() first")
        return self._connection

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        """A ``BEGIN IMMEDIATE`` transaction (reserved write lock taken at once)."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def _next_version(self, conn: sqlite3.Connection, table: str, project_id: str) -> int:
        row = conn.execute(
            f"SELECT COALESCE(MAX(version), 0) + 1 FROM {table} WHERE project_id = ?",  # noqa: S608 — table is a fixed literal
            (project_id,),
        ).fetchone()
        return int(row[0])

    def _require_project(self, conn: sqlite3.Connection, project_id: str) -> None:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise ValueError(f"no such project: {project_id!r}")

    def _reindex_paper_fts(self, conn: sqlite3.Connection, paper: Paper) -> None:
        conn.execute("DELETE FROM papers_fts WHERE paper_id = ?", (paper.id,))
        conn.execute(
            "INSERT INTO papers_fts(paper_id, title, abstract, raw_text) VALUES (?, ?, ?, ?)",
            (paper.id, paper.title or "", paper.abstract or "", paper.raw_text or ""),
        )

    def _reindex_analysis_fts(
        self,
        conn: sqlite3.Connection,
        paper_id: str,
        r1: Round1Analysis | None,
        r2: Round2Analysis | None,
    ) -> None:
        conn.execute("DELETE FROM analyses_fts WHERE paper_id = ?", (paper_id,))
        conn.execute(
            "INSERT INTO analyses_fts(paper_id, round1_text, round2_text) VALUES (?, ?, ?)",
            (paper_id, _round1_fts_text(r1), _round2_fts_text(r2)),
        )

    # --- lifecycle --------------------------------------------------------- #
    def init(self) -> None:
        if self._connection is not None:
            return
        if not self._is_memory:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(f"cannot create {self.db_path.parent}: {exc}") from exc
        try:
            conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        except sqlite3.Error as exc:
            raise RuntimeError(f"cannot open SQLite database {self.db_path}: {exc}") from exc
        conn.row_factory = sqlite3.Row

        row = conn.execute("PRAGMA quick_check(1)").fetchone()
        if row is not None and str(row[0]).lower() != "ok":
            conn.close()
            raise RuntimeError(f"database at {self.db_path} failed quick_check: {row[0]}")

        try:
            conn.execute("CREATE VIRTUAL TABLE temp.__strata_fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE temp.__strata_fts5_probe")
        except sqlite3.Error as exc:
            conn.close()
            raise RuntimeError(
                "this Python's SQLite was built without the FTS5 extension; "
                "use the official CPython (or a build with FTS5)"
            ) from exc

        if not self._is_memory:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        self._connection = conn
        self._migrate()

    def _migrate(self) -> None:
        conn = self._conn()
        conn.executescript(SCHEMA_SQL)
        row = conn.execute("SELECT value FROM project_meta WHERE key = 'schema_version'").fetchone()
        current = int(row[0]) if row and str(row[0]).isdigit() else 0
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{current} is newer than this strata-mcp "
                f"(supports v{SCHEMA_VERSION}); please update strata-mcp"
            )
        # (future: apply migrations for versions current+1 .. SCHEMA_VERSION)
        if current != SCHEMA_VERSION:
            conn.execute(
                "INSERT OR REPLACE INTO project_meta(key, value, updated_at) VALUES (?, ?, ?)",
                ("schema_version", str(SCHEMA_VERSION), now_iso()),
            )

    def health(self) -> dict[str, Any]:
        if self._connection is None:
            return {"ok": False, "initialised": False}
        conn = self._connection
        try:
            integ = conn.execute("PRAGMA quick_check(1)").fetchone()
            integrity_ok = integ is None or str(integ[0]).lower() == "ok"
            ver_row = conn.execute(
                "SELECT value FROM project_meta WHERE key = 'schema_version'"
            ).fetchone()
            counts = {
                "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "papers": conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
                "analyses": conn.execute("SELECT COUNT(*) FROM paper_analyses").fetchone()[0],
                "candidates": conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0],
            }
            queue = {
                status: conn.execute(
                    "SELECT COUNT(*) FROM ingest_queue WHERE status = ?", (status,)
                ).fetchone()[0]
                for status in ("pending", "processing", "done", "failed")
            }
        except sqlite3.Error as exc:
            return {"ok": False, "initialised": True, "error": str(exc)}
        return {
            "ok": bool(integrity_ok),
            "initialised": True,
            "db_path": str(self.db_path),
            "schema_version": int(ver_row[0]) if ver_row else None,
            "fts5": True,
            "integrity_ok": bool(integrity_ok),
            "counts": counts,
            "queue": queue,
        }

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # --- projects & phases ------------------------------------------------- #
    def create_project(self, project: Project) -> Project:
        with self._immediate() as conn:
            try:
                conn.execute(
                    "INSERT INTO projects(id, name, description, research_question, template, "
                    "repo_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        project.id,
                        project.name,
                        project.description,
                        project.research_question,
                        project.template,
                        project.repo_url,
                        project.created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"project id already exists: {project.id!r}") from exc
            for row in phase_catalogue.initial_phases(project.id):
                conn.execute(
                    "INSERT INTO phases(project_id, phase_number, name, status, started_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        row["project_id"],
                        row["phase_number"],
                        row["name"],
                        row["status"],
                        now_iso() if row["status"] == "in_progress" else None,
                    ),
                )
        return project

    def get_project(self, project_id: str) -> Project | None:
        row = self._conn().execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _row_to_project(row) if row else None

    def list_projects(self) -> list[Project]:
        rows = self._conn().execute("SELECT * FROM projects ORDER BY created_at, id").fetchall()
        return [_row_to_project(r) for r in rows]

    def set_phase(
        self, project_id: str, phase_number: int, status: str, notes: str | None = None
    ) -> None:
        if not phase_catalogue.is_valid_phase(phase_number):
            raise ValueError(f"unknown research phase: {phase_number}")
        if not phase_catalogue.is_valid_status(status):
            raise ValueError(
                f"unknown phase status {status!r}; valid: {', '.join(phase_catalogue.VALID_STATUSES)}"
            )
        now = now_iso()
        with self._immediate() as conn:
            self._require_project(conn, project_id)
            updated = conn.execute(
                """
                UPDATE phases
                   SET status = :status,
                       notes = COALESCE(:notes, notes),
                       started_at = CASE
                           WHEN :status IN ('in_progress', 'completed') AND started_at IS NULL
                           THEN :now ELSE started_at END,
                       completed_at = CASE
                           WHEN :status = 'completed' THEN :now
                           WHEN :status IN ('pending', 'in_progress') THEN NULL
                           ELSE completed_at END
                 WHERE project_id = :pid AND phase_number = :n
                """,
                {
                    "status": status,
                    "notes": notes,
                    "now": now,
                    "pid": project_id,
                    "n": phase_number,
                },
            ).rowcount
            if not updated:
                pdef = phase_catalogue.phase_def(phase_number)
                conn.execute(
                    "INSERT INTO phases(project_id, phase_number, name, status, started_at, "
                    "completed_at, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        phase_number,
                        pdef.name,
                        status,
                        now if status in ("in_progress", "completed") else None,
                        now if status == "completed" else None,
                        notes,
                    ),
                )

    def get_phases(self, project_id: str) -> list[dict[str, Any]]:
        rows = (
            self._conn()
            .execute(
                "SELECT phase_number, name, status, started_at, completed_at, notes "
                "FROM phases WHERE project_id = ? ORDER BY phase_number",
                (project_id,),
            )
            .fetchall()
        )
        return [dict(r) for r in rows]

    # --- papers & analysis ------------------------------------------------- #
    def _normalise_paper(self, paper: Paper) -> Paper:
        return Paper(
            id=paper.id,
            title=sanitize_text(paper.title) or paper.title,
            doi=normalize_doi(paper.doi) or paper.doi,
            arxiv_id=normalize_arxiv_id(paper.arxiv_id) or paper.arxiv_id,
            authors=paper.authors,
            year=paper.year,
            venue=paper.venue,
            url=normalize_url(paper.url) or paper.url,
            abstract=sanitize_text(paper.abstract),
            raw_text=sanitize_text(paper.raw_text),
            source=paper.source,
            ingested_at=paper.ingested_at,
        )

    def _find_existing_paper(self, conn: sqlite3.Connection, incoming: Paper, key: str):
        """Look up the row that already represents this paper: first by the
        computed dedupe key, then by a matching DOI / arXiv id (so a paper
        first stored under a weaker key, or now carrying a stronger id, still
        merges instead of duplicating)."""
        row = conn.execute("SELECT * FROM papers WHERE dedupe_key = ?", (key,)).fetchone()
        if row is not None:
            return row
        norm_doi = normalize_doi(incoming.doi)
        if norm_doi:
            row = conn.execute("SELECT * FROM papers WHERE doi = ?", (norm_doi,)).fetchone()
            if row is not None:
                return row
        norm_arxiv = normalize_arxiv_id(incoming.arxiv_id)
        if norm_arxiv:
            row = conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (norm_arxiv,)).fetchone()
            if row is not None:
                return row
        return None

    def save_paper(self, paper: Paper, project_id: str | None = None) -> Paper:
        incoming = self._normalise_paper(paper)
        key = dedupe_key_for(incoming)
        with self._immediate() as conn:
            if project_id is not None:
                self._require_project(conn, project_id)
            existing_row = self._find_existing_paper(conn, incoming, key)
            if existing_row is not None:
                existing = _row_to_paper(existing_row)
                merged = Paper(
                    id=existing.id,
                    title=incoming.title or existing.title,
                    doi=_coalesce(incoming.doi, existing.doi),
                    arxiv_id=_coalesce(incoming.arxiv_id, existing.arxiv_id),
                    authors=incoming.authors or existing.authors,
                    year=incoming.year or existing.year,
                    venue=_coalesce(incoming.venue, existing.venue),
                    url=_coalesce(incoming.url, existing.url),
                    abstract=_coalesce(incoming.abstract, existing.abstract),
                    raw_text=_coalesce(incoming.raw_text, existing.raw_text),
                    source=incoming.source
                    if incoming.source != PaperSource.UNKNOWN
                    else existing.source,
                    ingested_at=existing.ingested_at,
                )
                new_key = dedupe_key_for(merged)
                try:
                    conn.execute(
                        "UPDATE papers SET dedupe_key = ?, doi = ?, arxiv_id = ?, title = ?, "
                        "authors_json = ?, year = ?, venue = ?, url = ?, abstract = ?, "
                        "raw_text = ?, source = ? WHERE id = ?",
                        (
                            new_key,
                            merged.doi,
                            merged.arxiv_id,
                            merged.title,
                            _dumps(merged.authors),
                            merged.year,
                            merged.venue,
                            merged.url,
                            merged.abstract,
                            merged.raw_text,
                            merged.source.value,
                            merged.id,
                        ),
                    )
                except sqlite3.IntegrityError:
                    # the stronger key already belongs to another row; keep the old one
                    conn.execute(
                        "UPDATE papers SET doi = ?, arxiv_id = ?, title = ?, authors_json = ?, "
                        "year = ?, venue = ?, url = ?, abstract = ?, raw_text = ?, source = ? "
                        "WHERE id = ?",
                        (
                            merged.doi,
                            merged.arxiv_id,
                            merged.title,
                            _dumps(merged.authors),
                            merged.year,
                            merged.venue,
                            merged.url,
                            merged.abstract,
                            merged.raw_text,
                            merged.source.value,
                            merged.id,
                        ),
                    )
                self._reindex_paper_fts(conn, merged)
                result = merged
            else:
                conn.execute(
                    "INSERT INTO papers(id, dedupe_key, doi, arxiv_id, title, authors_json, year, "
                    "venue, url, abstract, raw_text, source, ingested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        incoming.id,
                        key,
                        incoming.doi,
                        incoming.arxiv_id,
                        incoming.title,
                        _dumps(incoming.authors),
                        incoming.year,
                        incoming.venue,
                        incoming.url,
                        incoming.abstract,
                        incoming.raw_text,
                        incoming.source.value,
                        incoming.ingested_at,
                    ),
                )
                self._reindex_paper_fts(conn, incoming)
                result = incoming

            if project_id is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_project(paper_id, project_id, "
                    "context_analysis_json, relevance_score, added_at) VALUES (?, ?, NULL, NULL, ?)",
                    (result.id, project_id, now_iso()),
                )
        return result

    def get_paper(self, paper_id: str, project_id: str | None = None) -> Paper | None:
        conn = self._conn()
        if project_id is None:
            row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT p.* FROM papers p JOIN paper_project pp ON pp.paper_id = p.id "
                "WHERE p.id = ? AND pp.project_id = ?",
                (paper_id, project_id),
            ).fetchone()
        return _row_to_paper(row) if row else None

    def list_papers(self, project_id: str) -> list[Paper]:
        rows = (
            self._conn()
            .execute(
                "SELECT p.* FROM papers p JOIN paper_project pp ON pp.paper_id = p.id "
                "WHERE pp.project_id = ? ORDER BY p.ingested_at, p.id",
                (project_id,),
            )
            .fetchall()
        )
        return [_row_to_paper(r) for r in rows]

    def find_paper_by_dedupe_key(self, key: str) -> Paper | None:
        row = self._conn().execute("SELECT * FROM papers WHERE dedupe_key = ?", (key,)).fetchone()
        return _row_to_paper(row) if row else None

    def save_paper_analysis(self, analysis: PaperAnalysis) -> None:
        with self._immediate() as conn:
            if (
                conn.execute("SELECT 1 FROM papers WHERE id = ?", (analysis.paper_id,)).fetchone()
                is None
            ):
                raise ValueError(f"no such paper: {analysis.paper_id!r}")
            existing = conn.execute(
                "SELECT round1_json, round2_json, model_used FROM paper_analyses WHERE paper_id = ?",
                (analysis.paper_id,),
            ).fetchone()
            r1 = analysis.round1
            r2 = analysis.round2
            r1_json = (
                _dumps(asdict(r1))
                if r1 is not None
                else (existing["round1_json"] if existing else None)
            )
            r2_json = (
                _dumps(asdict(r2))
                if r2 is not None
                else (existing["round2_json"] if existing else None)
            )
            model = analysis.model_used or (existing["model_used"] if existing else None)
            conn.execute(
                "INSERT INTO paper_analyses(paper_id, round1_json, round2_json, model_used, "
                "analyzed_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(paper_id) DO UPDATE SET round1_json = excluded.round1_json, "
                "round2_json = excluded.round2_json, model_used = excluded.model_used, "
                "analyzed_at = excluded.analyzed_at",
                (analysis.paper_id, r1_json, r2_json, model, analysis.analyzed_at or now_iso()),
            )
            eff_r1 = Round1Analysis(**_loads(r1_json)) if r1_json else None
            eff_r2 = Round2Analysis(**_loads(r2_json)) if r2_json else None
            self._reindex_analysis_fts(conn, analysis.paper_id, eff_r1, eff_r2)

    def get_paper_analysis(self, paper_id: str) -> PaperAnalysis | None:
        row = (
            self._conn()
            .execute("SELECT * FROM paper_analyses WHERE paper_id = ?", (paper_id,))
            .fetchone()
        )
        if row is None:
            return None
        r1 = _loads(row["round1_json"])
        r2 = _loads(row["round2_json"])
        return PaperAnalysis(
            paper_id=row["paper_id"],
            round1=Round1Analysis(**r1) if r1 else None,
            round2=Round2Analysis(**r2) if r2 else None,
            model_used=row["model_used"],
            analyzed_at=row["analyzed_at"],
        )

    def save_context(self, link: PaperProject) -> None:
        with self._immediate() as conn:
            self._require_project(conn, link.project_id)
            if (
                conn.execute("SELECT 1 FROM papers WHERE id = ?", (link.paper_id,)).fetchone()
                is None
            ):
                raise ValueError(f"no such paper: {link.paper_id!r}")
            conn.execute(
                "INSERT INTO paper_project(paper_id, project_id, context_analysis_json, "
                "relevance_score, added_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(paper_id, project_id) DO UPDATE SET "
                "context_analysis_json = excluded.context_analysis_json, "
                "relevance_score = excluded.relevance_score",
                (
                    link.paper_id,
                    link.project_id,
                    _dumps(link.context_analysis or {}),
                    link.relevance_score,
                    link.added_at,
                ),
            )

    def get_context(self, paper_id: str, project_id: str) -> PaperProject | None:
        row = (
            self._conn()
            .execute(
                "SELECT * FROM paper_project WHERE paper_id = ? AND project_id = ?",
                (paper_id, project_id),
            )
            .fetchone()
        )
        if row is None:
            return None
        return PaperProject(
            paper_id=row["paper_id"],
            project_id=row["project_id"],
            context_analysis=_loads(row["context_analysis_json"]) or {},
            relevance_score=row["relevance_score"],
            added_at=row["added_at"],
        )

    # --- search ------------------------------------------------------------ #
    def search(self, project_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        match = _fts_match_expr(query)
        if not match:
            return []
        limit = max(1, int(limit))
        conn = self._conn()
        best: dict[str, dict[str, Any]] = {}
        for src, table in (("paper", "papers_fts"), ("analysis", "analyses_fts")):
            try:
                rows = conn.execute(
                    f"""
                    SELECT f.paper_id AS pid,
                           bm25({table}) AS rank,
                           snippet({table}, -1, '[[', ']]', ' … ', 14) AS snip
                      FROM {table} f
                      JOIN paper_project pp ON pp.paper_id = f.paper_id AND pp.project_id = ?
                     WHERE {table} MATCH ?
                     ORDER BY rank
                     LIMIT ?
                    """,  # noqa: S608 — table/src are fixed literals
                    (project_id, match, limit * 4),
                ).fetchall()
            except sqlite3.OperationalError:
                # a pathological MATCH expression: be conservative, skip this index
                rows = []
            for r in rows:
                cur = best.get(r["pid"])
                if cur is None or r["rank"] < cur["rank"]:
                    best[r["pid"]] = {"rank": r["rank"], "matched_in": src, "snippet": r["snip"]}
        if not best:
            return []
        ordered = sorted(best, key=lambda pid: best[pid]["rank"])[:limit]
        out: list[dict[str, Any]] = []
        for pid in ordered:
            paper = self.get_paper(pid, project_id)
            if paper is None:
                continue
            info = best[pid]
            out.append(
                {
                    "paper_id": pid,
                    "title": paper.title,
                    "authors": paper.authors,
                    "year": paper.year,
                    "doi": paper.doi,
                    "arxiv_id": paper.arxiv_id,
                    "url": paper.url,
                    "score": round(-float(info["rank"]), 4),  # higher = more relevant
                    "matched_in": info["matched_in"],
                    "snippet": info["snippet"] or None,
                }
            )
        return out

    # --- ingest queue ------------------------------------------------------ #
    def queue_papers(
        self, project_id: str, urls: list[str], hint: str | None = None
    ) -> list[IngestQueueItem]:
        cleaned = [u.strip() for u in (urls or []) if u and u.strip()]
        if not cleaned:
            return []
        now = now_iso()
        results: list[IngestQueueItem] = []
        seen: set[str] = set()
        with self._immediate() as conn:
            self._require_project(conn, project_id)
            for url in cleaned:
                norm = normalize_url(url) or url
                if norm in seen:
                    continue
                seen.add(norm)
                conn.execute(
                    "INSERT OR IGNORE INTO ingest_queue(project_id, url, url_normalized, hint, "
                    "status, attempts, queued_at) VALUES (?, ?, ?, ?, 'pending', 0, ?)",
                    (project_id, url, norm, hint, now),
                )
                row = conn.execute(
                    "SELECT * FROM ingest_queue WHERE project_id = ? AND url_normalized = ? "
                    "AND status IN ('pending', 'processing') ORDER BY id LIMIT 1",
                    (project_id, norm),
                ).fetchone()
                if row is not None:
                    results.append(_row_to_queue_item(row))
        return results

    def dequeue_paper(self, worker_id: str) -> IngestQueueItem | None:
        worker_id = (worker_id or "worker").strip() or "worker"
        cutoff = self._stale_cutoff()
        now = now_iso()
        with self._immediate() as conn:
            # 1. reclaim items abandoned by a dead worker (or fail them if exhausted)
            conn.execute(
                """
                UPDATE ingest_queue
                   SET status = CASE WHEN attempts >= :maxa THEN 'failed' ELSE 'pending' END,
                       worker_id = NULL,
                       error = CASE WHEN error IS NULL THEN 'reclaimed: worker timed out' ELSE error END
                 WHERE status = 'processing' AND processed_at IS NOT NULL AND processed_at < :cutoff
                """,
                {"maxa": self.max_ingest_attempts, "cutoff": cutoff},
            )
            # 2. claim the oldest pending item
            row = conn.execute(
                "SELECT id FROM ingest_queue WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            qid = int(row["id"])
            conn.execute(
                "UPDATE ingest_queue SET status = 'processing', worker_id = ?, attempts = attempts + 1, "
                "processed_at = ?, error = NULL WHERE id = ?",
                (worker_id, now, qid),
            )
            claimed = conn.execute("SELECT * FROM ingest_queue WHERE id = ?", (qid,)).fetchone()
        return _row_to_queue_item(claimed) if claimed else None

    def _stale_cutoff(self) -> str:
        from datetime import datetime, timedelta, timezone

        return (
            datetime.now(timezone.utc) - timedelta(minutes=self.stale_queue_minutes)
        ).isoformat()

    def mark_ingested(self, queue_id: int) -> None:
        # clears ``raw`` too — once the paper is saved, the staged full text (see
        # ``stage_raw_text``) has served its purpose; no point keeping a second copy.
        with self._immediate() as conn:
            updated = conn.execute(
                "UPDATE ingest_queue SET status = 'done', processed_at = ?, error = NULL, "
                "raw = NULL WHERE id = ?",
                (now_iso(), int(queue_id)),
            ).rowcount
            if not updated:
                raise ValueError(f"no such ingest_queue item: {queue_id}")

    def mark_failed(
        self, queue_id: int, error: str, raw: str | None = None, permanent: bool = False
    ) -> None:
        with self._immediate() as conn:
            row = conn.execute(
                "SELECT attempts FROM ingest_queue WHERE id = ?", (int(queue_id),)
            ).fetchone()
            if row is None:
                raise ValueError(f"no such ingest_queue item: {queue_id}")
            terminal = permanent or int(row["attempts"]) >= self.max_ingest_attempts
            conn.execute(
                "UPDATE ingest_queue SET status = ?, error = ?, raw = COALESCE(?, raw), "
                "worker_id = NULL, processed_at = ? WHERE id = ?",
                (
                    "failed" if terminal else "pending",
                    (error or "").strip() or "unknown error",
                    sanitize_text(raw),
                    now_iso(),
                    int(queue_id),
                ),
            )

    def get_queue_item(self, queue_id: int) -> IngestQueueItem | None:
        row = (
            self._conn()
            .execute("SELECT * FROM ingest_queue WHERE id = ?", (int(queue_id),))
            .fetchone()
        )
        return _row_to_queue_item(row) if row else None

    def stage_raw_text(self, queue_id: int, text: str | None) -> None:
        """Park the full extracted ``raw_text`` of a queued paper in the queue
        row, so a subagent can fetch it once (server-side) and later persist it
        via ``save_paper`` without round-tripping the megabyte-sized text through
        its own context. ``mark_ingested`` clears it again."""
        with self._immediate() as conn:
            updated = conn.execute(
                "UPDATE ingest_queue SET raw = ? WHERE id = ?",
                (sanitize_text(text), int(queue_id)),
            ).rowcount
            if not updated:
                raise ValueError(f"no such ingest_queue item: {queue_id}")

    def get_staged_raw_text(self, queue_id: int) -> str | None:
        """The full ``raw_text`` staged for a queued paper by ``stage_raw_text``,
        or ``None`` if nothing was staged (or the item is gone)."""
        row = (
            self._conn()
            .execute("SELECT raw FROM ingest_queue WHERE id = ?", (int(queue_id),))
            .fetchone()
        )
        return row["raw"] if row and row["raw"] else None

    def queue_status(self, project_id: str) -> dict[str, int]:
        rows = (
            self._conn()
            .execute(
                "SELECT status, COUNT(*) AS n FROM ingest_queue WHERE project_id = ? GROUP BY status",
                (project_id,),
            )
            .fetchall()
        )
        out = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
        for r in rows:
            out[r["status"]] = int(r["n"])
        return out

    # --- gaps / literature reviews / drafts (append-only versions) --------- #
    def _save_versioned(self, table: str, project_id: str, content_md: str) -> tuple[int, int, str]:
        content = (content_md or "").strip()
        if not content:
            raise ValueError("content_md cannot be empty")
        now = now_iso()
        with self._immediate() as conn:
            self._require_project(conn, project_id)
            version = self._next_version(conn, table, project_id)
            cur = conn.execute(
                f"INSERT INTO {table}(project_id, version, content_md, created_at) "  # noqa: S608 — table is a fixed literal
                "VALUES (?, ?, ?, ?)",
                (project_id, version, content, now),
            )
            row_id = int(cur.lastrowid)
        return row_id, version, now

    def save_gap(self, project_id: str, content_md: str) -> Gap:
        row_id, version, now = self._save_versioned("gap_analyses", project_id, content_md)
        return Gap(
            id=row_id,
            project_id=project_id,
            version=version,
            content_md=content_md.strip(),
            created_at=now,
        )

    def get_gap(self, project_id: str, version: int | None = None) -> Gap | None:
        return self._get_versioned("gap_analyses", Gap, project_id, version)

    def save_literature_review(self, project_id: str, content_md: str) -> LiteratureReview:
        row_id, version, now = self._save_versioned("literature_reviews", project_id, content_md)
        return LiteratureReview(
            id=row_id,
            project_id=project_id,
            version=version,
            content_md=content_md.strip(),
            created_at=now,
        )

    def get_literature_review(
        self, project_id: str, version: int | None = None
    ) -> LiteratureReview | None:
        return self._get_versioned("literature_reviews", LiteratureReview, project_id, version)

    def _get_versioned(self, table: str, cls: Any, project_id: str, version: int | None) -> Any:
        conn = self._conn()
        if version is None:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE project_id = ? ORDER BY version DESC LIMIT 1",  # noqa: S608
                (project_id,),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE project_id = ? AND version = ?",  # noqa: S608
                (project_id, int(version)),
            ).fetchone()
        if row is None:
            return None
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            version=row["version"],
            content_md=row["content_md"],
            created_at=row["created_at"],
        )

    def save_draft(self, project_id: str, content_md: str, section: str | None = None) -> Draft:
        content = (content_md or "").strip()
        if not content:
            raise ValueError("content_md cannot be empty")
        # validate / normalise the section via the entity rules
        section = Draft(
            id=None, project_id=project_id or "x", version=0, content_md="x", section=section
        ).section
        now = now_iso()
        with self._immediate() as conn:
            self._require_project(conn, project_id)
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM drafts WHERE project_id = ? "
                "AND IFNULL(section, '') = IFNULL(?, '')",
                (project_id, section),
            ).fetchone()
            version = int(row[0])
            cur = conn.execute(
                "INSERT INTO drafts(project_id, version, section, content_md, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, version, section, content, now),
            )
            row_id = int(cur.lastrowid)
        return Draft(
            id=row_id,
            project_id=project_id,
            version=version,
            content_md=content,
            section=section,
            created_at=now,
        )

    def get_latest_draft(self, project_id: str, section: str | None = None) -> Draft | None:
        section = Draft(
            id=None, project_id=project_id or "x", version=0, content_md="x", section=section
        ).section
        row = (
            self._conn()
            .execute(
                "SELECT * FROM drafts WHERE project_id = ? AND IFNULL(section, '') = IFNULL(?, '') "
                "ORDER BY version DESC LIMIT 1",
                (project_id, section),
            )
            .fetchone()
        )
        if row is None:
            return None
        return Draft(
            id=row["id"],
            project_id=row["project_id"],
            version=row["version"],
            content_md=row["content_md"],
            section=row["section"],
            created_at=row["created_at"],
        )

    # --- scout candidates -------------------------------------------------- #
    def save_candidates(self, project_id: str, candidates: list[Candidate]) -> list[Candidate]:
        if not candidates:
            return []
        with self._immediate() as conn:
            self._require_project(conn, project_id)
            saved: list[Candidate] = []
            for cand in candidates:
                # honour the caller's project_id rather than whatever the object carried
                obj = Candidate(
                    id=cand.id,
                    project_id=project_id,
                    title=cand.title,
                    abstract=cand.abstract,
                    authors=cand.authors,
                    arxiv_id=cand.arxiv_id,
                    doi=cand.doi,
                    url=cand.url,
                    relevance_score=cand.relevance_score,
                    relevance_reason=cand.relevance_reason,
                    status=cand.status,
                    paper_id=cand.paper_id,
                    source=cand.source,
                    scouted_at=cand.scouted_at,
                )
                conn.execute(
                    "INSERT INTO candidates(id, project_id, title, abstract, authors_json, arxiv_id, "
                    "doi, url, relevance_score, relevance_reason, status, paper_id, source, scouted_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET title = excluded.title, abstract = excluded.abstract, "
                    "authors_json = excluded.authors_json, arxiv_id = excluded.arxiv_id, doi = excluded.doi, "
                    "url = excluded.url, relevance_score = excluded.relevance_score, "
                    "relevance_reason = excluded.relevance_reason, status = excluded.status, "
                    "source = excluded.source",
                    (
                        obj.id,
                        obj.project_id,
                        obj.title,
                        obj.abstract,
                        _dumps(obj.authors),
                        obj.arxiv_id,
                        obj.doi,
                        obj.url,
                        obj.relevance_score,
                        obj.relevance_reason,
                        obj.status.value,
                        obj.paper_id,
                        obj.source.value,
                        obj.scouted_at,
                    ),
                )
                saved.append(obj)
        return saved

    def list_candidates(self, project_id: str, status: str | None = None) -> list[Candidate]:
        conn = self._conn()
        if status is None:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE project_id = ? ORDER BY relevance_score DESC NULLS LAST, "
                "scouted_at, id",
                (project_id,),
            ).fetchall()
        else:
            CandidateStatus(status)  # validate
            rows = conn.execute(
                "SELECT * FROM candidates WHERE project_id = ? AND status = ? "
                "ORDER BY relevance_score DESC NULLS LAST, scouted_at, id",
                (project_id, status),
            ).fetchall()
        return [_row_to_candidate(r) for r in rows]

    def set_candidate_status(
        self, candidate_id: str, status: str, paper_id: str | None = None
    ) -> None:
        CandidateStatus(status)  # validate
        with self._immediate() as conn:
            if (
                paper_id is not None
                and conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
                is None
            ):
                raise ValueError(f"no such paper: {paper_id!r}")
            updated = conn.execute(
                "UPDATE candidates SET status = ?, paper_id = COALESCE(?, paper_id) WHERE id = ?",
                (status, paper_id, candidate_id),
            ).rowcount
            if not updated:
                raise ValueError(f"no such candidate: {candidate_id!r}")

    # --- repo snapshots ---------------------------------------------------- #
    def save_repo_snapshot(self, snapshot: RepoSnapshot) -> None:
        with self._immediate() as conn:
            self._require_project(conn, snapshot.project_id)
            conn.execute(
                "INSERT INTO repo_snapshots(project_id, repo_url, layers_json, scanned_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET repo_url = excluded.repo_url, "
                "layers_json = excluded.layers_json, scanned_at = excluded.scanned_at",
                (
                    snapshot.project_id,
                    snapshot.repo_url,
                    _dumps(snapshot.layers or {}),
                    snapshot.scanned_at,
                ),
            )

    def get_repo_snapshot(self, project_id: str) -> RepoSnapshot | None:
        row = (
            self._conn()
            .execute("SELECT * FROM repo_snapshots WHERE project_id = ?", (project_id,))
            .fetchone()
        )
        if row is None:
            return None
        return RepoSnapshot(
            project_id=row["project_id"],
            repo_url=row["repo_url"],
            layers=_loads(row["layers_json"]) or {},
            scanned_at=row["scanned_at"],
        )
