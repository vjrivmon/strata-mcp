"""strata-mcp — MCP stdio server.

Exposes ``mcp__strata__strata_*`` tools that let Claude Code drive a local
research project: manage projects/phases, queue and ingest papers, store the
two-round analyses, run FTS5 search, persist gap analyses / literature reviews /
drafts, run the arXiv / Semantic Scholar scout, and introspect a code repo.

There is NO LLM here — the reasoning model is Claude Code itself (the session,
plus Haiku subagents for bulk paper analysis launched from ``/strata``). This
process only stores data, fetches papers and runs external searches.

Run it with the ``strata-mcp`` console script or ``python -m strata_mcp``; it is
registered in ``~/.claude.json`` by ``scripts/install.sh``.

Scaffold note: the transport skeleton is here; the ~24 tools (see the project
wiki, "Tools MCP strata_*") are wired in phases 6-8 once core + adapters exist.
"""

from __future__ import annotations

import asyncio
import sys

# Imported here so a missing dependency fails loudly at startup.
from mcp.server import Server  # noqa: F401
from mcp.server.stdio import stdio_server  # noqa: F401
from mcp.types import TextContent, Tool  # noqa: F401

from strata_mcp import __version__

SERVER_NAME = "strata"

# TODO(phase 6-8): build the Server, register the strata_* tools, dispatch to
# core use-cases over an IStorage / IPaperSource / IRepoSource wired from env.


def _require_python_310() -> None:
    if sys.version_info < (3, 10):  # noqa: UP036  -- guards against being run under an older interpreter
        sys.exit(f"strata-mcp requires Python 3.10+ (found {sys.version.split()[0]})")


async def _run() -> None:
    """Placeholder event loop. Replaced in phase 6-8 by the real MCP server."""
    raise SystemExit(
        f"strata-mcp {__version__}: scaffold only — the MCP server is implemented in phases 6-8."
    )


def main() -> None:
    """Console-script / ``python -m strata_mcp`` entry point."""
    _require_python_310()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
