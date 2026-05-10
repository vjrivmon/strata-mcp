"""Unit tests for :mod:`strata_mcp.core.entities` — the small validators, the
``__post_init__`` coercion, and the ``create()`` factories."""

from __future__ import annotations

import pytest

from strata_mcp.core import entities as E
from strata_mcp.core.entities import (
    Candidate,
    CandidateStatus,
    IngestQueueItem,
    IngestStatus,
    Paper,
    PaperAnalysis,
    PaperProject,
    PaperSource,
    Project,
    Round1Analysis,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_new_id_is_unique_hex():
    a, b = E.new_id(), E.new_id()
    assert a != b
    assert len(a) == 32 and all(c in "0123456789abcdef" for c in a)


def test_now_iso_is_utc_isoformat():
    ts = E.now_iso()
    assert ts.endswith("+00:00")


@pytest.mark.parametrize("value", [0, 5, 10, 7.5, "8.2", None])
def test_coerce_score_accepts_valid(value):
    out = E.coerce_score(value)
    assert out is None if value is None else 0.0 <= out <= 10.0


@pytest.mark.parametrize("value", [-0.1, 10.1, 11, "x", float("nan")])
def test_coerce_score_rejects_invalid(value):
    with pytest.raises(ValueError):
        E.coerce_score(value, field_name="relevance_score")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2021, 2021), ("2019", 2019), ("nope", None), (None, None), (3000, None), (12, None)],
)
def test_coerce_year(value, expected):
    assert E.coerce_year(value) == expected


def test_normalize_authors_trims_dedupes_preserves_order():
    assert E.normalize_authors(["  A ", "B", "a", "", None, "C"]) == ["A", "B", "C"]
    assert E.normalize_authors("Solo Author") == ["Solo Author"]
    assert E.normalize_authors(None) == []


# --------------------------------------------------------------------------- #
# Project
# --------------------------------------------------------------------------- #
def test_project_create_mints_id_and_defaults():
    p = Project.create("My Thesis")
    assert p.id and p.name == "My Thesis"
    assert p.template == "generic"
    assert p.created_at.endswith("+00:00")


def test_project_create_validates_template_and_name():
    with pytest.raises(ValueError):
        Project.create("   ")
    with pytest.raises(ValueError):
        Project.create("ok", template="springer")
    assert Project.create("ok", template="LNCS").template == "lncs"


def test_project_post_init_requires_id():
    with pytest.raises(ValueError):
        Project(id="", name="x")


# --------------------------------------------------------------------------- #
# Paper
# --------------------------------------------------------------------------- #
def test_paper_create_normalises_fields():
    p = Paper.create(
        " A Title ",
        authors=["X", "x", " Y "],
        year="2020",
        source="arxiv",
        arxiv_id="2401.00001",
    )
    assert p.id and p.title == "A Title"
    assert p.authors == ["X", "Y"]
    assert p.year == 2020
    assert p.source is PaperSource.ARXIV


def test_paper_post_init_coerces_source_string_and_rejects_blank_title():
    p = Paper(id="x", title="T", source="web")
    assert p.source is PaperSource.WEB
    with pytest.raises(ValueError):
        Paper(id="x", title="  ")
    with pytest.raises(ValueError):
        Paper(id="x", title="T", source="not-a-source")


def test_paper_create_bad_year_becomes_none():
    assert Paper.create("T", year="last tuesday").year is None


# --------------------------------------------------------------------------- #
# analyses
# --------------------------------------------------------------------------- #
def test_round1_cleans_bullets_and_checks_score():
    r = Round1Analysis(bullets=[" one ", "", "two", None], relevance_score="6")
    assert r.bullets == ["one", "two"]
    assert r.relevance_score == 6.0
    with pytest.raises(ValueError):
        Round1Analysis(relevance_score=99)


def test_paper_analysis_requires_paper_id():
    PaperAnalysis(paper_id="p1", model_used="haiku")
    with pytest.raises(ValueError):
        PaperAnalysis(paper_id="")


def test_paper_project_validates_ids_and_score():
    link = PaperProject(paper_id="p", project_id="proj", relevance_score=4)
    assert link.relevance_score == 4.0
    with pytest.raises(ValueError):
        PaperProject(paper_id="p", project_id="", relevance_score=1)
    with pytest.raises(ValueError):
        PaperProject(paper_id="p", project_id="proj", relevance_score=-1)


# --------------------------------------------------------------------------- #
# IngestQueueItem
# --------------------------------------------------------------------------- #
def test_ingest_queue_item_coerces_status_and_clamps_attempts():
    item = IngestQueueItem(
        id=None, project_id="proj", url="https://x", status="pending", attempts=-3
    )
    assert item.status is IngestStatus.PENDING
    assert item.attempts == 0
    with pytest.raises(ValueError):
        IngestQueueItem(id=None, project_id="proj", url="")
    with pytest.raises(ValueError):
        IngestQueueItem(id=None, project_id="proj", url="u", status="weird")


# --------------------------------------------------------------------------- #
# Candidate
# --------------------------------------------------------------------------- #
def test_candidate_create_and_post_init():
    c = Candidate.create(
        "proj", " Found Paper ", authors=["A", "a"], relevance_score=9, source="arxiv"
    )
    assert c.id and c.project_id == "proj" and c.title == "Found Paper"
    assert c.authors == ["A"]
    assert c.relevance_score == 9.0
    assert c.status is CandidateStatus.PENDING
    assert c.source is PaperSource.ARXIV


def test_candidate_post_init_coerces_status_string():
    c = Candidate(id="c1", project_id="p", title="t", status="approved")
    assert c.status is CandidateStatus.APPROVED
    with pytest.raises(ValueError):
        Candidate(id="c1", project_id="p", title="t", status="banished")
    with pytest.raises(ValueError):
        Candidate(id="", project_id="p", title="t")


# --------------------------------------------------------------------------- #
# Draft section coercion
# --------------------------------------------------------------------------- #
def test_draft_section_coercion():
    from strata_mcp.core.entities import Draft

    assert Draft(id=None, project_id="p", version=1, content_md="x").section is None
    assert Draft(id=None, project_id="p", version=1, content_md="x", section="  ").section is None
    assert (
        Draft(id=None, project_id="p", version=1, content_md="x", section="Introduction").section
        == "introduction"
    )
    with pytest.raises(ValueError):
        Draft(id=None, project_id="p", version=1, content_md="x", section="appendix-q")


def test_repo_snapshot_requires_project_and_url():
    from strata_mcp.core.entities import RepoSnapshot

    snap = RepoSnapshot(project_id="p", repo_url="https://github.com/x/y")
    assert snap.layers == {} and snap.scanned_at.endswith("+00:00")
    with pytest.raises(ValueError):
        RepoSnapshot(project_id="", repo_url="https://x")
    with pytest.raises(ValueError):
        RepoSnapshot(project_id="p", repo_url="  ")
