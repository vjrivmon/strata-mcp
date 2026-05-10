# Contributing to strata-mcp

Thanks for taking a look. This is a young, open project and contributions of all
kinds are welcome: bug reports, feature ideas, documentation, tests, and code.

## Ground rules

- Be respectful and assume good faith. Discussions stay technical and friendly.
- This repo is **public**, so never commit secrets — no API keys, no tokens, no
  `.env`. Only `.env.example` (with placeholders) is tracked.
- The engine has **no LLM and no API key inside it** by design. If a change
  needs an LLM, it belongs in a skill (instructions for Claude Code), not in the
  Python.

## Getting set up

```bash
git clone https://github.com/vjrivmon/strata-mcp.git
cd strata-mcp
./scripts/install.sh          # .venv + pip install -e ".[dev]"
.venv/bin/pytest              # run the tests
.venv/bin/ruff check .        # lint
```

Python 3.10+ is required.

## Architecture in one paragraph

Hexagonal. `src/strata_mcp/core/` is pure domain (entities, the `IStorage` /
`IPaperSource` / `IRepoSource` ports, the workflow phases, paper dedupe) with no
external dependencies. `src/strata_mcp/adapters/` implements those ports
(SQLite + FTS5 storage; arXiv / Semantic Scholar / PDF / web paper sources;
GitHub repo introspection). `src/strata_mcp/server.py` is the MCP transport — it
registers `strata_*` tools and dispatches into the core. `skills/` and
`commands/strata.md` are instructions for Claude Code, not code that runs here.

## Making a change

1. Open an issue first for anything non-trivial, so we can agree on the approach.
2. Branch from `main`.
3. Keep changes focused. Match the surrounding style. Add or update tests.
4. Run `ruff check .` and `pytest` before pushing.
5. Use clear commit messages — [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`) are appreciated.
6. Open a PR describing what changed and why. Link the issue.

## What's most useful right now

The repo is at the scaffold stage. The roadmap (MVP → v1 → v2) lives in the
design docs; in short, the MVP needs: the SQLite storage adapter (+ FTS5), the
arXiv and PDF adapters, the core dedupe logic, the MCP server with the core
tools, and the `analyze-paper` / `gap-analysis` / `draft-paper` skills. Tests for
the queue (atomic dequeue, stale-`processing` reclaim, retry cap) and the schema
are especially welcome.

## Code of conduct

Treat everyone with respect. Harassment or discrimination of any kind isn't
tolerated. If something's off, contact the maintainer.
