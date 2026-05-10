"""Unit tests for :mod:`strata_mcp.core.phases` — the research-workflow phase
catalogue and its look-up / seed helpers."""

from __future__ import annotations

import pytest

from strata_mcp.core import phases as P
from strata_mcp.core.entities import ResearchPhase


def test_catalogue_is_contiguous_named_and_matches_enum():
    numbers = [p.number for p in P.RESEARCH_PHASES]
    assert numbers == list(range(len(P.RESEARCH_PHASES)))
    assert numbers == [e.value for e in ResearchPhase]
    keys = [p.key for p in P.RESEARCH_PHASES]
    assert len(set(keys)) == len(keys)  # unique
    for p in P.RESEARCH_PHASES:
        assert p.name and p.key and p.summary
    assert P.FIRST_PHASE == 0
    assert P.LAST_PHASE == numbers[-1]


def test_phase_def_and_phase_by_key_roundtrip():
    for p in P.RESEARCH_PHASES:
        assert P.phase_def(p.number) is p
        assert P.phase_by_key(p.key) is p
    with pytest.raises(ValueError):
        P.phase_def(99)
    with pytest.raises(ValueError):
        P.phase_by_key("nope")


def test_is_valid_helpers():
    assert P.is_valid_phase(0) and not P.is_valid_phase(-1) and not P.is_valid_phase(99)
    assert P.is_valid_status("in_progress") and not P.is_valid_status("done-ish")
    assert set(P.VALID_STATUSES) == {"pending", "in_progress", "completed", "skipped"}


def test_next_phase():
    assert P.next_phase(0).number == 1
    assert P.next_phase(P.LAST_PHASE) is None
    with pytest.raises(ValueError):
        P.next_phase(99)


def test_initial_phases_seeds_all_pending_except_setup_in_progress():
    rows = P.initial_phases("proj-1")
    assert len(rows) == len(P.RESEARCH_PHASES)
    assert all(r["project_id"] == "proj-1" for r in rows)
    by_num = {r["phase_number"]: r for r in rows}
    assert by_num[0]["status"] == "in_progress"
    assert by_num[0]["name"] == "Setup"
    assert all(by_num[n]["status"] == "pending" for n in by_num if n != 0)
