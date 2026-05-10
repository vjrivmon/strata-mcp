"""strata-mcp — MCP stdio server.

Exposes ``mcp__strata__strata_*`` tools that let Claude Code drive a local
research project: manage projects/phases, queue + ingest papers, store the
two-round analyses, run FTS5 search, persist gap analyses / literature reviews /
drafts, run the arXiv scout, and read/write scout candidates.

There is **no LLM here** — the reasoning model is Claude Code itself (the
session, plus Haiku subagents for bulk paper analysis launched from
``/strata``). This process only stores data, fetches papers and runs the arXiv
search. Storage = ``./.strata/strata.db`` relative to the directory the server
runs in (overridable with the ``STRATA_DB`` environment variable), mirroring how
apex keeps ``.apex/context.db``.

Run it with the ``strata-mcp`` console script or ``python -m strata_mcp``; it is
registered in ``~/.claude.json`` by ``scripts/install.sh``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from strata_mcp import __version__
from strata_mcp.adapters.arxiv import ArxivSource
from strata_mcp.adapters.pdf_extractor import PdfSource
from strata_mcp.adapters.sqlite_storage import (
    DEFAULT_DB_PATH,
    MAX_INGEST_ATTEMPTS_DEFAULT,
    STALE_QUEUE_MINUTES_DEFAULT,
    SqliteStorage,
)
from strata_mcp.core.entities import (
    Candidate,
    Paper,
    PaperAnalysis,
    PaperProject,
    PaperSource,
    Project,
    Round1Analysis,
    Round2Analysis,
)
from strata_mcp.core.phases import RESEARCH_PHASES
from strata_mcp.core.ports import IStorage

SERVER_NAME = "strata"

mcp = FastMCP(
    SERVER_NAME,
    instructions=(
        "strata-mcp: a per-project SQLite library of papers (analysed in two rounds), "
        "gap analysis, drafts, and an arXiv scout. strata-mcp stores and fetches; Claude "
        "Code does the reasoning. Drive it through the /strata command. The database lives "
        "at ./.strata/strata.db (or $STRATA_DB)."
    ),
)


# --------------------------------------------------------------------------- #
# Storage wiring (lazy, single shared connection)
# --------------------------------------------------------------------------- #
_storage: IStorage | None = None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def get_storage() -> IStorage:
    global _storage
    if _storage is None:
        store = SqliteStorage(
            os.environ.get("STRATA_DB") or DEFAULT_DB_PATH,
            stale_queue_minutes=_env_int("STRATA_STALE_QUEUE_MINUTES", STALE_QUEUE_MINUTES_DEFAULT),
            max_ingest_attempts=_env_int("STRATA_MAX_INGEST_ATTEMPTS", MAX_INGEST_ATTEMPTS_DEFAULT),
        )
        store.init()
        _storage = store
    return _storage


def bind_storage(storage: IStorage | None) -> None:
    """Replace the shared storage (used by the installer's smoke check and the
    test-suite; production wires it lazily from the environment). The new
    storage is ``init()``-ed eagerly (``init`` is idempotent)."""
    global _storage
    if _storage is not None and storage is not _storage:
        try:
            _storage.close()
        except Exception:  # noqa: BLE001
            pass
    if storage is not None:
        storage.init()
    _storage = storage


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #
def _d(obj: Any) -> dict:
    """A pure-JSON dict for a dataclass instance (enums -> their string value)."""
    return json.loads(json.dumps(asdict(obj), ensure_ascii=False, default=str))


def _paper_with_context(storage: IStorage, paper: Paper, project_id: str | None) -> dict:
    out = _d(paper)
    if project_id:
        link = storage.get_context(paper.id, project_id)
        out["context"] = _d(link) if link else None
        analysis = storage.get_paper_analysis(paper.id)
        out["analysis"] = _d(analysis) if analysis else None
    return out


def _round1(data: dict | None) -> Round1Analysis | None:
    if not data:
        return None
    return Round1Analysis(
        bullets=list(data.get("bullets") or []),
        relevance_score=data.get("relevance_score"),
        worth_reading=data.get("worth_reading"),
        summary_es=data.get("summary_es"),
    )


def _round2(data: dict | None) -> Round2Analysis | None:
    if not data:
        return None
    keys = (
        "intro_summary",
        "related_work",
        "methodology",
        "results",
        "strengths",
        "limitations",
        "key_contributions",
    )
    return Round2Analysis(**{k: data.get(k) for k in keys})


# --------------------------------------------------------------------------- #
# Status / health
# --------------------------------------------------------------------------- #
@mcp.tool()
def strata_health() -> dict:
    """Diagnostics for the strata database: schema version, integrity, FTS5
    availability, row counts and ingest-queue stats. Use this first if anything
    looks off."""
    return {"version": __version__, **get_storage().health()}


@mcp.tool()
def strata_get_status(project_id: str | None = None) -> dict:
    """High-level status. Without a project id: the version, db path and the list
    of projects. With one: that project, its phases, paper/queue counts."""
    storage = get_storage()
    base: dict[str, Any] = {
        "version": __version__,
        "db_path": os.environ.get("STRATA_DB") or DEFAULT_DB_PATH,
        "projects": [_d(p) for p in storage.list_projects()],
    }
    if project_id:
        project = storage.get_project(project_id)
        if project is None:
            raise ValueError(f"no such project: {project_id!r}")
        base["project"] = _d(project)
        base["phases"] = storage.get_phases(project_id)
        base["paper_count"] = len(storage.list_papers(project_id))
        base["queue"] = storage.queue_status(project_id)
    return base


@mcp.tool()
def strata_init_project() -> dict:
    """Ensure the strata database (``./.strata/strata.db`` or ``$STRATA_DB``)
    exists and is healthy. Idempotent. Run once at the start of a session before
    creating a project."""
    return {"version": __version__, **get_storage().health()}


# --------------------------------------------------------------------------- #
# Projects & phases
# --------------------------------------------------------------------------- #
@mcp.tool()
def strata_create_project(
    name: str,
    description: str | None = None,
    research_question: str | None = None,
    template: str = "generic",
    repo_url: str | None = None,
) -> dict:
    """Create a research project (a paper/thesis with its own library). ``template``
    is one of lncs|ieee|acm|inted|generic. Returns the project with its generated id."""
    project = Project.create(
        name,
        description=description,
        research_question=research_question,
        template=template,
        repo_url=repo_url,
    )
    return _d(get_storage().create_project(project))


@mcp.tool()
def strata_list_projects() -> list[dict]:
    """All research projects in the database."""
    return [_d(p) for p in get_storage().list_projects()]


@mcp.tool()
def strata_get_project(project_id: str) -> dict:
    """A project plus its phase states."""
    storage = get_storage()
    project = storage.get_project(project_id)
    if project is None:
        raise ValueError(f"no such project: {project_id!r}")
    return {**_d(project), "phases": storage.get_phases(project_id)}


@mcp.tool()
def strata_set_phase(
    project_id: str, phase_number: int, status: str, notes: str | None = None
) -> dict:
    """Update a research-workflow phase (0..9): status is pending|in_progress|
    completed|skipped. Phases mirror the /strata flow (0 setup, 1 library, 2
    relevance, 3 gap, 4 scout, 5 literature review, 6 draft, 7 qa, 8 export, 9
    iteration)."""
    storage = get_storage()
    storage.set_phase(project_id, phase_number, status, notes)
    return {"ok": True, "phases": storage.get_phases(project_id)}


@mcp.tool()
def strata_list_phases() -> list[dict]:
    """The catalogue of research-workflow phases (number, key, name, summary) —
    the same phases ``strata_set_phase`` tracks per project."""
    return [
        {"number": p.number, "key": p.key, "name": p.name, "summary": p.summary}
        for p in RESEARCH_PHASES
    ]


# --------------------------------------------------------------------------- #
# Ingest queue (drained by Haiku subagents launched from /strata)
# --------------------------------------------------------------------------- #
@mcp.tool()
def strata_queue_papers(project_id: str, urls: list[str], hint: str | None = None) -> dict:
    """Enqueue paper references (arXiv ids/URLs, direct PDF URLs, local PDF
    paths) for ingestion into a project's library. Duplicates (by normalised
    URL) against still-active queue items are skipped. ``hint`` (arxiv|pdf) is an
    optional source hint. Returns the queued items + queue stats; drain the queue
    with subagents (dequeue -> fetch -> analyse -> save -> mark_ingested)."""
    storage = get_storage()
    items = storage.queue_papers(project_id, urls, hint=hint)
    return {"queued": [_d(i) for i in items], "queue": storage.queue_status(project_id)}


@mcp.tool()
def strata_queue_status(project_id: str) -> dict:
    """Ingest-queue counts for a project: pending / processing / done / failed."""
    return get_storage().queue_status(project_id)


@mcp.tool()
def strata_dequeue_paper(worker_id: str = "subagent") -> dict | None:
    """Atomically claim the oldest pending ingest item for processing (also
    reclaims items abandoned by a dead worker). Returns the item (with its
    ``url`` and ``id``) or ``null`` if the queue is empty. Call this from a
    Haiku subagent, then ``strata_fetch_paper_text`` on the url."""
    item = get_storage().dequeue_paper(worker_id)
    return _d(item) if item else None


@mcp.tool()
def strata_mark_ingested(queue_id: int) -> dict:
    """Mark an ingest-queue item as done (after its paper + analyses are saved)."""
    get_storage().mark_ingested(queue_id)
    return {"ok": True}


@mcp.tool()
def strata_mark_failed(queue_id: int, error: str, raw: str | None = None) -> dict:
    """Record that an ingest item failed (404, unreadable PDF, ...). It is
    retried until the attempt cap, then permanently ``failed``. ``raw`` may hold
    a snippet of what was fetched, for debugging."""
    get_storage().mark_failed(queue_id, error, raw=raw)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Fetching paper text (no LLM)
# --------------------------------------------------------------------------- #
_PAPER_SOURCES = (ArxivSource(), PdfSource())


@mcp.tool()
def strata_fetch_paper_text(ref: str, hint: str | None = None) -> dict:
    """Fetch a paper's metadata + full text from a reference (arXiv id/URL, a
    direct PDF URL, or a local PDF path). Returns title/doi/arxiv_id/authors/
    year/venue/url/abstract/raw_text plus ``raw_text_truncated`` (a shorter
    version sized for a Haiku context window — use it for round-1 analysis).
    Web-page and Semantic-Scholar sources are not in this MVP. Raises a
    descriptive error on a hard failure (404, scanned/encrypted PDF, ...)."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("ref is required")
    chosen = None
    if hint:
        chosen = next((s for s in _PAPER_SOURCES if s.name == hint.strip().lower()), None)
    if chosen is None:
        chosen = next((s for s in _PAPER_SOURCES if s.can_handle(ref)), None)
    if chosen is None:
        raise ValueError(
            f"no paper source can handle {ref!r} (this MVP supports arXiv ids/URLs and PDF "
            "files/URLs; web pages, DOIs and Semantic Scholar are v1)"
        )
    return _d(chosen.fetch(ref))


# --------------------------------------------------------------------------- #
# Papers, analyses, context
# --------------------------------------------------------------------------- #
@mcp.tool()
def strata_save_paper(
    title: str,
    project_id: str | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    authors: list[str] | None = None,
    year: int | None = None,
    venue: str | None = None,
    url: str | None = None,
    abstract: str | None = None,
    raw_text: str | None = None,
    source: str = "unknown",
) -> dict:
    """Save (upsert by DOI > arXiv id > URL > title) a paper into the library;
    if ``project_id`` is given, also link it to that project. Re-saving the same
    paper merges new metadata into the existing row (it never duplicates).
    Returns the stored paper — **use the returned ``id``** for analyses/context."""
    paper = Paper.create(
        title,
        doi=doi,
        arxiv_id=arxiv_id,
        authors=authors,
        year=year,
        venue=venue,
        url=url,
        abstract=abstract,
        raw_text=raw_text,
        source=source if source in {s.value for s in PaperSource} else "unknown",
    )
    return _d(get_storage().save_paper(paper, project_id=project_id))


@mcp.tool()
def strata_get_paper(paper_id: str, project_id: str | None = None) -> dict:
    """A paper by id. With ``project_id`` the lookup is scoped to that project's
    library and the result also carries its per-project ``context`` and its
    two-round ``analysis``."""
    storage = get_storage()
    paper = storage.get_paper(paper_id, project_id=project_id)
    if paper is None:
        raise ValueError(f"no such paper{' in this project' if project_id else ''}: {paper_id!r}")
    return _paper_with_context(storage, paper, project_id)


@mcp.tool()
def strata_list_papers(project_id: str, include_analysis: bool = False) -> list[dict]:
    """Every paper in a project's library. With ``include_analysis`` each paper
    carries its per-project context + two-round analysis (heavier payload)."""
    storage = get_storage()
    papers = storage.list_papers(project_id)
    if not include_analysis:
        return [_d(p) for p in papers]
    return [_paper_with_context(storage, p, project_id) for p in papers]


@mcp.tool()
def strata_save_paper_analysis(
    paper_id: str,
    round1: dict | None = None,
    round2: dict | None = None,
    model_used: str | None = None,
) -> dict:
    """Persist a paper's analysis. ``round1`` = {bullets[], relevance_score(0-10),
    worth_reading(bool), summary_es}; ``round2`` = {intro_summary, related_work,
    methodology, results, strengths, limitations, key_contributions}. Either may
    be omitted — saving round2 later does not erase round1. ``model_used`` e.g.
    ``haiku``."""
    if round1 is None and round2 is None:
        raise ValueError("provide at least one of round1, round2")
    analysis = PaperAnalysis(
        paper_id=paper_id, round1=_round1(round1), round2=_round2(round2), model_used=model_used
    )
    get_storage().save_paper_analysis(analysis)
    return {"ok": True, "paper_id": paper_id}


@mcp.tool()
def strata_get_paper_analysis(paper_id: str) -> dict | None:
    """The stored two-round analysis of a paper, or ``null`` if none yet."""
    analysis = get_storage().get_paper_analysis(paper_id)
    return _d(analysis) if analysis else None


@mcp.tool()
def strata_save_context(
    paper_id: str, project_id: str, context_analysis: dict, relevance_score: float | None = None
) -> dict:
    """Save the project-specific relevance of a paper. ``context_analysis`` =
    {contribution_to_project, gaps_covered[], gaps_not_covered[]}; ``relevance_score``
    0-10. The paper must already be in the library."""
    link = PaperProject(
        paper_id=paper_id,
        project_id=project_id,
        context_analysis=context_analysis or {},
        relevance_score=relevance_score,
    )
    get_storage().save_context(link)
    return {"ok": True}


@mcp.tool()
def strata_get_context(paper_id: str, project_id: str) -> dict | None:
    """A paper's project-specific context analysis, or ``null``."""
    link = get_storage().get_context(paper_id, project_id)
    return _d(link) if link else None


# --------------------------------------------------------------------------- #
# Full-text search (FTS5 BM25 — replaces ChromaDB)
# --------------------------------------------------------------------------- #
@mcp.tool()
def strata_search(project_id: str, query: str, limit: int = 10) -> list[dict]:
    """Full-text search a project's library — papers (title/abstract/raw_text)
    and analyses — ranked by BM25. Each hit: paper_id, title, authors, year,
    ids/url, ``score`` (higher = better), ``matched_in`` (paper|analysis) and a
    ``snippet``. An empty / whitespace query returns ``[]``."""
    return get_storage().search(project_id, query, limit=limit)


# --------------------------------------------------------------------------- #
# Gaps / literature reviews / drafts (append-only versions)
# --------------------------------------------------------------------------- #
@mcp.tool()
def strata_save_gap(project_id: str, content_md: str) -> dict:
    """Save a gap analysis (Markdown) for a project. A new monotone version is
    created each time (older versions are kept)."""
    return _d(get_storage().save_gap(project_id, content_md))


@mcp.tool()
def strata_get_gap(project_id: str, version: int | None = None) -> dict | None:
    """The latest gap analysis for a project (or a specific ``version``), or ``null``."""
    gap = get_storage().get_gap(project_id, version)
    return _d(gap) if gap else None


@mcp.tool()
def strata_save_literature_review(project_id: str, content_md: str) -> dict:
    """Save a literature review / related-work write-up (Markdown). Append-only versions."""
    return _d(get_storage().save_literature_review(project_id, content_md))


@mcp.tool()
def strata_get_literature_review(project_id: str, version: int | None = None) -> dict | None:
    """The latest literature review for a project (or a specific ``version``), or ``null``."""
    lr = get_storage().get_literature_review(project_id, version)
    return _d(lr) if lr else None


@mcp.tool()
def strata_save_draft(project_id: str, content_md: str, section: str | None = None) -> dict:
    """Save a paper draft (Markdown). ``section`` (abstract|introduction|
    related_work|methodology|results|discussion|conclusion) scopes it to one
    section; omit it for a full draft. Each (project, section) gets its own
    monotone version sequence; older versions are kept."""
    return _d(get_storage().save_draft(project_id, content_md, section=section))


@mcp.tool()
def strata_get_latest_draft(project_id: str, section: str | None = None) -> dict | None:
    """The latest draft for a project (or for one ``section``), or ``null``."""
    draft = get_storage().get_latest_draft(project_id, section=section)
    return _d(draft) if draft else None


# --------------------------------------------------------------------------- #
# Scout: arXiv search + candidates
# --------------------------------------------------------------------------- #
@mcp.tool()
def strata_search_arxiv(query: str, max_results: int = 20) -> dict:
    """Search arXiv for papers. Returns ``{query, hits:[{title, abstract,
    authors, year, arxiv_id, doi, url, source}], error}``. Use this in the scout
    phase, then rank and ``strata_save_candidates``. (Semantic Scholar search is
    v1.)"""
    result = ArxivSource().search(query, max_results=max_results)
    return _d(result)


@mcp.tool()
def strata_save_candidates(project_id: str, candidates: list[dict]) -> list[dict]:
    """Save scout candidates for a project. Each candidate: {title (required),
    abstract, authors[], arxiv_id, doi, url, relevance_score(0-10),
    relevance_reason, source}. Returns the stored candidates with generated ids;
    they start ``pending`` for approve/reject."""
    objs = []
    for c in candidates or []:
        if not isinstance(c, dict) or not (c.get("title") or "").strip():
            raise ValueError("each candidate must be an object with a non-empty 'title'")
        src = c.get("source") or "unknown"
        objs.append(
            Candidate.create(
                project_id,
                c["title"],
                abstract=c.get("abstract"),
                authors=c.get("authors"),
                arxiv_id=c.get("arxiv_id"),
                doi=c.get("doi"),
                url=c.get("url"),
                relevance_score=c.get("relevance_score"),
                relevance_reason=c.get("relevance_reason"),
                source=src if src in {s.value for s in PaperSource} else "unknown",
            )
        )
    return [_d(c) for c in get_storage().save_candidates(project_id, objs)]


@mcp.tool()
def strata_list_candidates(project_id: str, status: str | None = None) -> list[dict]:
    """Scout candidates for a project, ordered by relevance. Optional ``status``
    filter: pending|approved|rejected|already_in_library."""
    return [_d(c) for c in get_storage().list_candidates(project_id, status)]


@mcp.tool()
def strata_approve_candidate(candidate_id: str, project_id: str) -> dict:
    """Approve a scout candidate: mark it ``approved`` and re-queue its arXiv
    id/URL into the project's ingest queue so a subagent picks it up. The
    candidate must have an arxiv_id or url."""
    storage = get_storage()
    cands = {c.id: c for c in storage.list_candidates(project_id)}
    cand = cands.get(candidate_id)
    if cand is None:
        raise ValueError(f"no such candidate in this project: {candidate_id!r}")
    ref = cand.arxiv_id or cand.url
    if not ref:
        raise ValueError(f"candidate {candidate_id!r} has no arxiv_id or url to ingest")
    queued = storage.queue_papers(project_id, [ref], hint="arxiv" if cand.arxiv_id else None)
    storage.set_candidate_status(candidate_id, "approved")
    return {
        "ok": True,
        "queued": [_d(i) for i in queued],
        "queue": storage.queue_status(project_id),
    }


@mcp.tool()
def strata_reject_candidate(candidate_id: str) -> dict:
    """Reject a scout candidate (mark it ``rejected``)."""
    get_storage().set_candidate_status(candidate_id, "rejected")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def _require_python_310() -> None:
    if sys.version_info < (3, 10):  # noqa: UP036 - guards against an older interpreter
        sys.exit(f"strata-mcp requires Python 3.10+ (found {sys.version.split()[0]})")


def main() -> None:
    """Console-script / ``python -m strata_mcp`` entry point: serve over stdio."""
    _require_python_310()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
