# CLAUDE.md — strata-mcp

Context for any AI agent (Claude, Codex, ...) working on this repo.

## What this is

`strata-mcp` = the **engine** of an academic-research workflow, built like the
`apex` repo: an MCP stdio server + `skills/` + a `commands/strata.md` slash
command + `templates/`. Persistence: `./.strata/strata.db` (SQLite + FTS5) per
research-project directory, mirroring `.apex/context.db`.

**The reasoning model is Claude Code**, not anything inside this repo:
- Heavy reasoning (gap analysis, drafting section by section, literature review,
  scout ranking, citation QA) → the Claude Code session (Opus/Sonnet).
- Bulk, mechanical paper analysis (round 1 + round 2 for many papers) → **Haiku
  subagents** (`Task` tool, `model="haiku"`) draining a SQLite ingest queue, a
  batch at a time.
- `strata-mcp` itself → **no AI**. Stores data, fetches papers (arXiv/DOI/PDF/
  web), runs FTS5 search, runs arXiv/Semantic Scholar searches, manages phases,
  introspects a code repo.

It deliberately drops, vs. the previous "Strata": the remote FastAPI backend,
Ollama/VRAIN, Supabase/Postgres, ChromaDB/embeddings, and the web UI.

This repo is **public / open-source** (MIT). Never commit secrets — `.env` is
gitignored; only `.env.example` is tracked.

## Project history & design docs

This repo was scaffolded from a `/apex` design session. The full design (the
"why", the decisions, the C4 diagrams, the data model, the `/strata` flow, the
edge cases, the checkpoints) lives **in the `strata` repo**, not here:
- `~/RoadToDevOps/strata/STRATA-MCP-BLUEPRINT.md`
- `~/RoadToDevOps/strata/HANDOFF.md`
- `~/RoadToDevOps/strata/.apex/` (APEX context.db + wiki — `SPEC`,
  `entities/data-model`, `concepts/value-proposition`, `EDGE-CASES`)

The APEX tracking for this work stays in `~/RoadToDevOps/strata/.apex/` until
strata-mcp is mature enough to self-host (like `apex/` does). Do **not** run
`apex_init_project` here.

## Architecture (hexagonal)

```
src/strata_mcp/
├── server.py          # MCP stdio transport; registers strata_* tools, dispatches to core
├── core/              # PURE domain — no external deps
│   ├── entities.py    # Project, Paper, PaperAnalysis, PaperProject, Gap,
│   │                  #   LiteratureReview, Draft, Candidate, IngestQueueItem, RepoSnapshot + enums
│   ├── ports.py       # IStorage, IPaperSource, IRepoSource (+ FetchedPaper, SearchResult VOs)
│   ├── phases.py      # the /strata workflow phase catalogue (0..9)
│   └── dedupe.py      # paper dedupe: doi -> arxiv_id(no version) -> url -> normalised title
└── adapters/          # concrete impls of the ports — the ONLY place externals live
    ├── sqlite_storage.py     # IStorage over SQLite + FTS5 (schema, migrations, triggers, WAL)
    ├── arxiv.py              # IPaperSource — arXiv API (always HTTPS) + abs/pdf pages
    ├── semantic_scholar.py   # IPaperSource — Semantic Scholar Graph API (429 backoff)
    ├── pdf_extractor.py      # IPaperSource — local/downloaded PDF (defensive: magic bytes, size, scanned/encrypted)
    ├── web_scraper.py        # IPaperSource — generic landing page (meta-tag fallbacks)
    └── github_repo.py        # IRepoSource — layered repo introspection (GITHUB_TOKEN or gh CLI)
```

Rule: change DB → only `sqlite_storage.py`. Add a paper source → a new
`IPaperSource` impl. The domain never imports an adapter.

The non-Python deliverables: `skills/*/SKILL.md` (instructions for Claude Code,
distilled from the prompts in the old `apps/backend/domain/agents/*.py`),
`commands/strata.md` (the phased flow, mirror of `/apex`), `templates/{lncs,
ieee,acm,inted,generic}/` (paper templates), `scripts/install.sh` (venv +
register MCP + link skills/command), `scripts/migrate_supabase.py` (one-shot
Supabase → SQLite, v1).

## Conventions

- **No emojis** anywhere (output, code, docs). Hard rule.
- All prompts/skills: "Responde SIEMPRE en español" (the user is Spanish).
- Anti-hallucination: the analysis/draft skills forbid inventing data; the draft
  cites only papers in the library; post-generation filter scans citation keys.
- Parsing JSON from a subagent: `re.search(r'\{[\s\S]*\}', resp)` — they may add
  stray text or `<think>` blocks.
- Errors are never swallowed with a generic message: `logger.error` with the
  full traceback, include the real detail in the response.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`,
  `test:`). CI green before declaring an implementation phase done.
- Python 3.10+. Dependencies are pure-Python wheels only: `mcp`, `httpx`,
  `pypdf`, `feedparser`, `beautifulsoup4` (dev: `pytest`, `pytest-cov`, `ruff`).
- src-layout: imports are `from strata_mcp.core... import ...`. Do **not** put a
  top-level `mcp/` package in the repo root (it would shadow the `mcp` pip pkg).

## Hardening baked into the design (see EDGE-CASES wiki)

- Ingest queue: atomic `dequeue_paper` (`BEGIN IMMEDIATE` + claim one pending
  row), stale-`processing` reclaim (dead worker), `attempts` cap, UNIQUE on
  active `(project_id, url_normalized)`.
- `fetch_paper_text` returns `raw_text` *and* `raw_text_truncated` (sized for a
  Haiku context window). Round 1 and round 2 are saved separately.
- SQLite: `journal_mode=WAL` + `busy_timeout`, `quick_check` on open, FTS5
  availability probed on init, `papers_fts`/`analyses_fts` kept in sync by AFTER
  triggers, `strata_search` sanitises the FTS5 query, `raw_text` sanitised
  (NUL/control chars, NFKC) before storage, append-only `version` computed in a
  transaction, `schema_version` in `project_meta` + idempotent migrations, every
  paper/gap/draft/candidate query scoped by `project_id`.
- PDF extractor: path checks, `%PDF-` magic, size cap, "scanned PDF" detection
  (too little text → hard fail, no OCR), encrypted → hard fail.

## Open question to resolve early (phase 5/6 spike)

Do `Task(model="haiku")` subagents inherit the session's MCP servers (i.e. can
they call `mcp__strata__strata_*`)? If yes → subagents call the tools directly
(Plan A). If no → subagents receive the text in the prompt and *return* the
round-1/round-2 JSON; the orchestrator (main session) does the `strata_*` writes
(Plan B). The `analyze-paper` skill is written to work either way.

## Status

Scaffold (phase 5 of the /apex flow). Structure, schema and stubs are in place;
nothing is implemented yet. Next: phase 6 (core) → phase 7 (adapters) → phase 8
(integration / wire up the MCP tools) → phase 9 (tests, ≥80% on core).
