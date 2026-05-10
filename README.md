# strata-mcp

> Academic research engine as a standalone **MCP server** — a per-project SQLite
> library of papers analysed in two rounds, gap analysis, drafts, and an
> arXiv / Semantic Scholar scout. **Claude Code is the reasoning model.** No
> remote LLM, no API keys.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
![Status: alpha (MVP)](https://img.shields.io/badge/status-alpha-orange.svg)

> **Heads up — early MVP.** The core engine works: the SQLite+FTS5 library, the
> ingest queue, the arXiv source, the `strata_*` MCP tools, the installer and the
> `/strata` command are implemented and tested. Some skills
> (`relevance-analysis`, `literature-review`, `scout`, `citation-qa`) and some
> sources (Semantic Scholar, web scraper, repo introspection), the paper
> templates and the Supabase migration are still stubs. Issues and PRs are very
> welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## What it is

`strata-mcp` is the *engine* of a small research workflow, built the same way
[`apex`](https://github.com/) is built: a Model Context Protocol (MCP) server +
a set of [skills](skills/) + a slash command. You run it inside the directory of
a thesis or paper you're writing; Claude Code talks to it; it keeps everything
in a local `./.strata/strata.db` (SQLite + FTS5), just like `.apex/context.db`.

The split of work:

- **strata-mcp** (this repo) — stores data, fetches papers (arXiv / DOI / PDF /
  web), runs full-text search (FTS5 BM25), runs arXiv / Semantic Scholar
  searches, manages workflow phases, introspects a code repo. **No AI inside.**
- **Claude Code** — the reasoning model. Heavy reasoning (gap analysis, drafting
  section by section, literature review, ranking scout results, citation QA) is
  done by your session (Opus/Sonnet). The bulk, mechanical work (analysing many
  papers in two rounds) is done by **Haiku subagents** draining a queue, a few
  at a time, on your Claude subscription — no API key.

It replaces, for this workflow: a remote FastAPI backend, a slow self-hosted
Ollama, Supabase/Postgres, ChromaDB/embeddings, and clicking around a web UI —
with one local SQLite file and a conversation.

## The `/strata` workflow

`Setup → Library → Relevance → Gap analysis → Scout → Literature review → Draft
→ QA → Export → Iteration` — phase-by-phase, like `/apex`.

```
you ── /strata ──▶ Claude Code ──▶ strata-mcp ──▶ ./.strata/strata.db
                        │                └──▶ arXiv / Semantic Scholar / PDFs / a code repo
                        └── Haiku subagents drain the ingest queue (round-1 + round-2 analysis)
```

## Quickstart

```bash
git clone https://github.com/vjrivmon/strata-mcp.git
cd strata-mcp
./scripts/install.sh          # creates .venv, pip install -e ".[dev]",
                              # registers the `strata` MCP server in ~/.claude.json,
                              # links skills/ and commands/strata.md into ~/.claude/
# restart Claude Code, then in your thesis/paper directory:
/strata
```

Configuration is all optional — see [`.env.example`](.env.example). There are no
required external services and no API keys.

## Layout

```
strata-mcp/
├── src/strata_mcp/
│   ├── server.py            # MCP stdio server (entry point: `strata-mcp`)
│   ├── core/                # pure domain: entities, ports, phases, dedupe
│   └── adapters/            # sqlite_storage (+FTS5), arxiv, semantic_scholar,
│                            #   pdf_extractor, web_scraper, github_repo
├── skills/                  # SKILL.md files that teach Claude Code each step
├── commands/strata.md       # the /strata slash command (phased flow)
├── templates/               # paper templates: lncs, ieee, acm, inted, generic
├── scripts/                 # install.sh, migrate_supabase.py
└── tests/
```

Hexagonal: the domain depends only on the ports in `core/ports.py`. Swap SQLite
for another DB → only `adapters/sqlite_storage.py` changes. Add a paper source
(PubMed, bioRxiv) → a new `IPaperSource` implementation.

## Development

```bash
./scripts/install.sh
.venv/bin/ruff check .
.venv/bin/pytest
```

## Contributing

Contributions are welcome — bug reports, ideas, docs, and code. Start with
[CONTRIBUTING.md](CONTRIBUTING.md). Be kind; assume good faith.

## License

[MIT](LICENSE) © 2026 Vicente Rivas Monferrer
