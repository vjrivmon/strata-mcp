# Changelog

All notable changes to strata-mcp are recorded here. This project follows
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-05-10

First working release — the MVP engine.

### Added
- **MCP stdio server** (`strata-mcp`) exposing 33 `strata_*` tools: project /
  phase management, the ingest queue, paper text fetching, two-round paper
  analyses, per-project relevance context, FTS5 full-text search, versioned gap
  analyses / literature reviews / drafts, and an arXiv scout (search + candidate
  approve/reject). No LLM inside — Claude Code is the reasoning model.
- **SQLite + FTS5 storage** (`./.strata/strata.db` per research project): schema
  + idempotent migration scaffold, WAL + busy_timeout + foreign keys + integrity
  check + FTS5 probe on open; papers deduplicated/upserted by DOI > arXiv id >
  URL > normalised title with metadata merge; an ingest queue with atomic
  dequeue (`BEGIN IMMEDIATE`), stale-worker reclaim and an attempts cap;
  BM25 search over papers and analyses (`OR`-joined `\w+` tokens, project-scoped);
  `raw_text`/abstracts sanitised (NFKC, control chars) before storage.
- **Paper sources**: arXiv (Atom API metadata + PDF full text, HTTPS-only, retry
  + backoff on 429/5xx, degrades to metadata-only if the PDF fails) and a
  defensive PDF extractor (magic bytes, size cap via `STRATA_MAX_PDF_MB`,
  scanned/encrypted detection, `STRATA_HTTP_TIMEOUT` / `STRATA_CONTACT_EMAIL`).
- **`/strata` slash command** — a phased research workflow (setup → library →
  relevance → gap → scout → literature review → draft → QA → export → iteration),
  with Haiku subagents draining the ingest queue in parallel.
- **Skills**: `analyze-paper`, `gap-analysis`, `draft-paper` (real; distilled
  from the prior agents). `relevance-analysis`, `literature-review`, `scout`,
  `citation-qa` ship as v1 placeholders.
- **`scripts/install.sh`** — venv + editable install, registers the `strata` MCP
  server in `~/.claude.json` (with backup), symlinks the skills and command into
  `~/.claude/`. Idempotent.
- Tests: 132 passing (core 100%, total ~93%); CI on Python 3.10 and 3.12.

### Not yet implemented (planned for v1)
- The Semantic Scholar, generic web-scraper and GitHub repo-introspection
  adapters; the `relevance-analysis` / `literature-review` / `scout` /
  `citation-qa` skills; the paper templates (`templates/{lncs,ieee,acm,inted,generic}/`);
  the Supabase → SQLite migration (`scripts/migrate_supabase.py`); integration
  with a strata-hub aggregator.

[0.1.0]: https://github.com/vjrivmon/strata-mcp/releases/tag/v0.1.0
