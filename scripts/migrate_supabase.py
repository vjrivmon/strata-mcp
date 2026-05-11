#!/usr/bin/env python3
"""migrate_supabase.py — one-shot migration of the original Strata data from
Supabase (Postgres) into per-project ``.strata/strata.db`` SQLite databases.

Reads from Supabase via PostgREST (no ``supabase`` SDK needed — just ``httpx``,
already a dependency): ``projects``, ``papers`` (incl. ``round1_analysis`` /
``round2_analysis`` / ``raw_text``), ``paper_project`` (``context_analysis``,
``relevance_score``), ``drafts``, ``gap_analyses``, ``candidates``. For each
project it creates ``<dest>/<project-slug>/.strata/strata.db`` and loads that
project's papers + analyses + per-project context + gaps + drafts + candidates.

Idempotent at project granularity: a project that already exists in its target
DB is left alone (so re-running won't duplicate gap/draft versions); papers are
upserted by dedupe key regardless. A paper with no ``round2_analysis`` (it
failed in the original ingest) is migrated anyway — re-queue it from ``/strata``
to re-analyse.

Credentials come from the environment — ``SUPABASE_URL`` plus one of
``SUPABASE_SERVICE_ROLE_KEY`` / ``SUPABASE_SERVICE_KEY`` / ``SUPABASE_KEY`` /
``SUPABASE_ANON_KEY`` (see ``.env.example``). Nothing is written to the repo.

Usage:
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \\
        python scripts/migrate_supabase.py --dest ~/RoadToDevOps
    python scripts/migrate_supabase.py --dest ~/RoadToDevOps --dry-run
    python scripts/migrate_supabase.py --only <project-id> --dest ~/RoadToDevOps
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from strata_mcp.adapters.sqlite_storage import SqliteStorage
from strata_mcp.core.entities import (
    VALID_TEMPLATES,
    Candidate,
    Paper,
    PaperAnalysis,
    PaperProject,
    PaperSource,
    Project,
    Round1Analysis,
    Round2Analysis,
    coerce_score,
    coerce_year,
    normalize_authors,
    now_iso,
)

_PAGE_LIMIT = 50_000  # these are personal research tables — a single page is plenty
_KEY_ENV_VARS = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_KEY",
    "SUPABASE_ANON_KEY",
)
_ROUND2_KEYS = (
    "intro_summary",
    "related_work",
    "methodology",
    "results",
    "strengths",
    "limitations",
    "key_contributions",
)


# --------------------------------------------------------------------------- #
# Supabase / PostgREST access
# --------------------------------------------------------------------------- #
def _credentials() -> tuple[str, str]:
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = next((os.environ[v].strip() for v in _KEY_ENV_VARS if os.environ.get(v)), "")
    if not url or not key:
        raise SystemExit(
            "migrate_supabase: set SUPABASE_URL and a key "
            f"(one of: {', '.join(_KEY_ENV_VARS)}) — see .env.example"
        )
    return url, key


def _fetch_table(client, base: str, table: str, order: str | None) -> list[dict]:
    """All rows of ``table`` (one page; warns if the page is full). Returns
    ``[]`` if the table doesn't exist in this Supabase project."""
    params: dict[str, object] = {"select": "*", "limit": _PAGE_LIMIT}
    if order:
        params["order"] = order
    resp = client.get(f"{base}/rest/v1/{table}", params=params)
    if resp.status_code in (404, 406):
        return []
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        return []
    if len(rows) >= _PAGE_LIMIT:
        print(
            f"  ! warning: {table} returned {_PAGE_LIMIT}+ rows — increase _PAGE_LIMIT",
            file=sys.stderr,
        )
    return rows


# --------------------------------------------------------------------------- #
# Coercion helpers (be lenient: a one-shot migration shouldn't crash on a quirk)
# --------------------------------------------------------------------------- #
def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "project"


def _safe_score(value: object) -> float | None:
    try:
        return coerce_score(value)
    except (ValueError, TypeError):
        return None


def _template(value: object) -> str:
    t = (str(value).strip().lower() if value else "") or "generic"
    return t if t in VALID_TEMPLATES else "generic"


def _source(value: object) -> str:
    v = str(value).strip().lower() if value else ""
    return v if v in {s.value for s in PaperSource} else "unknown"


def _round1(data: object) -> Round1Analysis | None:
    if not isinstance(data, dict) or not data:
        return None
    return Round1Analysis(
        bullets=list(data.get("bullets") or []),
        relevance_score=_safe_score(data.get("relevance_score")),
        worth_reading=data.get("worth_reading"),
        summary_es=data.get("summary_es"),
    )


def _round2(data: object) -> Round2Analysis | None:
    if not isinstance(data, dict) or not data:
        return None
    if not any(data.get(k) for k in _ROUND2_KEYS):
        return None
    return Round2Analysis(**{k: data.get(k) for k in _ROUND2_KEYS})


def _project_from_row(row: dict) -> Project:
    return Project(
        id=str(row["id"]),
        name=row.get("name") or f"Project {row['id']}",
        description=row.get("description"),
        research_question=row.get("research_question"),
        template=_template(row.get("template")),
        repo_url=row.get("repo_url"),
        created_at=row.get("created_at") or now_iso(),
    )


def _paper_from_row(row: dict) -> Paper | None:
    title = (row.get("title") or "").strip()
    if not title:
        return None
    return Paper(
        id=str(row["id"]),
        title=title,
        doi=row.get("doi"),
        arxiv_id=row.get("arxiv_id"),
        authors=normalize_authors(row.get("authors")),
        year=coerce_year(row.get("year")),
        venue=row.get("venue"),
        url=row.get("url"),
        abstract=row.get("abstract"),
        raw_text=row.get("raw_text"),
        source=_source(row.get("source")),
    )


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #
def _migrate_project(
    proj_row: dict,
    *,
    papers_by_id: dict[str, dict],
    links_by_project: dict[str, list[dict]],
    drafts_by_project: dict[str, list[dict]],
    gaps_by_project: dict[str, list[dict]],
    cands_by_project: dict[str, list[dict]],
    dest: Path,
    used_slugs: set[str],
    dry_run: bool,
) -> None:
    pid = str(proj_row["id"])
    name = proj_row.get("name") or pid
    slug = _slug(name)
    if slug in used_slugs:
        slug = f"{slug}-{pid[:6]}"
    used_slugs.add(slug)
    db_path = dest / slug / ".strata" / "strata.db"

    links = links_by_project.get(pid, [])
    drafts = sorted(
        drafts_by_project.get(pid, []), key=lambda d: (d.get("version") or 0, d.get("id") or 0)
    )
    gaps = sorted(
        gaps_by_project.get(pid, []), key=lambda g: (g.get("created_at") or "", g.get("id") or 0)
    )
    cands = cands_by_project.get(pid, [])

    print(f"\n• {name}  [{pid}]")
    print(f"  -> {db_path}")
    print(
        f"  papers={len(links)} drafts={len(drafts)} gaps={len(gaps)} candidates={len(cands)}"
        + ("  (dry-run, not written)" if dry_run else "")
    )
    if dry_run:
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = SqliteStorage(db_path)
    storage.init()
    try:
        if storage.get_project(pid) is None:
            storage.create_project(_project_from_row(proj_row))
            print("  created project")
        else:
            print("  project already present — refreshing papers only")

        # papers + per-project context + analyses
        n_papers = n_analyses = 0
        for link in links:
            paper_row = papers_by_id.get(str(link.get("paper_id")))
            if not paper_row:
                continue
            paper = _paper_from_row(paper_row)
            if paper is None:
                print(f"  ! skipped paper {link.get('paper_id')} (no title)", file=sys.stderr)
                continue
            stored = storage.save_paper(paper, project_id=pid)
            n_papers += 1
            storage.save_context(
                PaperProject(
                    paper_id=stored.id,
                    project_id=pid,
                    context_analysis=link.get("context_analysis") or {},
                    relevance_score=_safe_score(link.get("relevance_score")),
                )
            )
            r1, r2 = (
                _round1(paper_row.get("round1_analysis")),
                _round2(paper_row.get("round2_analysis")),
            )
            if r1 or r2:
                storage.save_paper_analysis(PaperAnalysis(paper_id=stored.id, round1=r1, round2=r2))
                n_analyses += 1
        print(f"  migrated {n_papers} papers, {n_analyses} analyses")

        # gaps / drafts — append-only; only seed if the project has none yet
        if gaps and storage.get_gap(pid) is None:
            for g in gaps:
                content = g.get("content") or g.get("content_md")
                if content:
                    storage.save_gap(pid, content)
            print(f"  migrated {len(gaps)} gap analyses")
        elif gaps:
            print("  gap analyses already present — skipped")

        if drafts and storage.get_latest_draft(pid) is None:
            for d in drafts:
                content = d.get("content_md") or d.get("content")
                if content:
                    storage.save_draft(pid, content, section=d.get("section"))
            print(f"  migrated {len(drafts)} drafts")
        elif drafts:
            print("  drafts already present — skipped")

        # candidates — only seed if the project has none yet
        if cands and not storage.list_candidates(pid):
            objs = []
            for c in cands:
                if not (c.get("title") or "").strip():
                    continue
                objs.append(
                    Candidate.create(
                        pid,
                        c["title"],
                        abstract=c.get("abstract"),
                        authors=c.get("authors"),
                        arxiv_id=c.get("arxiv_id"),
                        doi=c.get("doi"),
                        url=c.get("url"),
                        relevance_score=_safe_score(c.get("relevance_score")),
                        relevance_reason=c.get("relevance_reason"),
                        source=_source(c.get("source")),
                    )
                )
            saved = storage.save_candidates(pid, objs)
            kept = [c for c in cands if (c.get("title") or "").strip()]
            for new_c, old_c in zip(saved, kept, strict=True):
                status = (old_c.get("status") or "pending").strip().lower()
                if status in ("approved", "rejected", "already_in_library"):
                    storage.set_candidate_status(new_c.id, status)
            print(f"  migrated {len(saved)} candidates")
        elif cands:
            print("  candidates already present — skipped")
    finally:
        storage.close()


def _index(rows: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r.get(key)), []).append(r)
    return out


def run(dest: Path, *, only: str | None = None, dry_run: bool = False) -> int:
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        raise SystemExit("migrate_supabase: httpx is not installed") from None

    base, key = _credentials()
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}
    dest = dest.expanduser().resolve()
    print(f"Source: {base}\nDest:   {dest}{'  (dry run)' if dry_run else ''}")

    with httpx.Client(timeout=60.0, headers=headers, follow_redirects=True) as client:
        projects = _fetch_table(client, base, "projects", order="created_at")
        if not projects:
            print("no projects found in Supabase — nothing to migrate.", file=sys.stderr)
            return 1
        papers = _fetch_table(client, base, "papers", order=None)
        links = _fetch_table(client, base, "paper_project", order=None)
        drafts = _fetch_table(client, base, "drafts", order=None)
        gaps = _fetch_table(client, base, "gap_analyses", order=None)
        cands = _fetch_table(client, base, "candidates", order=None)

    if only:
        projects = [p for p in projects if str(p.get("id")) == only]
        if not projects:
            raise SystemExit(f"migrate_supabase: no project with id {only!r}")

    papers_by_id = {str(p["id"]): p for p in papers if p.get("id") is not None}
    links_by_project = _index(links, "project_id")
    drafts_by_project = _index(drafts, "project_id")
    gaps_by_project = _index(gaps, "project_id")
    cands_by_project = _index(cands, "project_id")

    used_slugs: set[str] = set()
    for proj in projects:
        _migrate_project(
            proj,
            papers_by_id=papers_by_id,
            links_by_project=links_by_project,
            drafts_by_project=drafts_by_project,
            gaps_by_project=gaps_by_project,
            cands_by_project=cands_by_project,
            dest=dest,
            used_slugs=used_slugs,
            dry_run=dry_run,
        )
    print(f"\nDone — {len(projects)} project(s){' (dry run, nothing written)' if dry_run else ''}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Strata data Supabase -> SQLite (.strata/)."
    )
    parser.add_argument("--dest", default=".", help="parent directory for the per-project dirs")
    parser.add_argument("--only", default=None, help="migrate only this project id")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be migrated, write nothing"
    )
    args = parser.parse_args(argv)
    return run(Path(args.dest), only=args.only, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
