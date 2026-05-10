"""Smoke tests: the package imports cleanly (incl. every adapter and the server
module) and exposes a version. Focused unit coverage of the core lives in
``test_dedupe.py`` / ``test_entities.py`` / ``test_phases.py``; storage + queue
land with the adapter in phase 7."""

from __future__ import annotations

import strata_mcp
from strata_mcp.core import dedupe
from strata_mcp.core.entities import ResearchPhase
from strata_mcp.core.phases import RESEARCH_PHASES, is_valid_phase, phase_def


def test_package_has_version():
    assert isinstance(strata_mcp.__version__, str)
    assert strata_mcp.__version__


def test_modules_import():
    # adapters and the server module must at least import cleanly
    import strata_mcp.adapters.arxiv  # noqa: F401
    import strata_mcp.adapters.github_repo  # noqa: F401
    import strata_mcp.adapters.pdf_extractor  # noqa: F401
    import strata_mcp.adapters.semantic_scholar  # noqa: F401
    import strata_mcp.adapters.sqlite_storage  # noqa: F401
    import strata_mcp.adapters.web_scraper  # noqa: F401
    import strata_mcp.core.ports  # noqa: F401
    import strata_mcp.server  # noqa: F401


def test_phase_catalogue_is_contiguous_and_named():
    numbers = [p.number for p in RESEARCH_PHASES]
    assert numbers == list(range(len(RESEARCH_PHASES)))
    assert numbers == [p.value for p in ResearchPhase]
    for p in RESEARCH_PHASES:
        assert p.name and p.key and p.summary
        assert phase_def(p.number) is p
    assert is_valid_phase(0)
    assert not is_valid_phase(99)


def test_dedupe_normalisers():
    assert dedupe.normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert dedupe.normalize_doi("doi:10.5/x") == "10.5/x"
    assert dedupe.normalize_doi(None) is None
    assert dedupe.normalize_arxiv_id("https://arxiv.org/abs/2401.12345v3") == "2401.12345"
    assert dedupe.normalize_arxiv_id("2401.12345") == "2401.12345"
    assert dedupe.normalize_title("  The  {\\em Foo}: A Bar! ") == "the em foo a bar"
    base = "https://example.com/p/1"
    assert dedupe.normalize_url(base + "/?utm_source=x#frag") == base
