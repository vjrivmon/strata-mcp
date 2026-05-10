"""Unit tests for :class:`strata_mcp.adapters.sqlite_storage.SqliteStorage`.

Everything runs against a temp-file DB (so WAL + ``foreign_keys`` behave as in
production); the queue / dedupe / FTS / versioning paths are all exercised
without touching the network.
"""

from __future__ import annotations

import pytest

from strata_mcp.adapters import sqlite_storage as S
from strata_mcp.adapters.sqlite_storage import SqliteStorage, sanitize_text
from strata_mcp.core.entities import (
    Candidate,
    Paper,
    PaperAnalysis,
    PaperProject,
    Project,
    RepoSnapshot,
    Round1Analysis,
    Round2Analysis,
)


@pytest.fixture
def store(tmp_path):
    st = SqliteStorage(
        tmp_path / ".strata" / "strata.db", stale_queue_minutes=15, max_ingest_attempts=3
    )
    st.init()
    yield st
    st.close()


@pytest.fixture
def project(store):
    p = Project.create("Transformers Thesis", research_question="Why attention?", template="lncs")
    store.create_project(p)
    return p


# --------------------------------------------------------------------------- #
# lifecycle & helpers
# --------------------------------------------------------------------------- #
def test_init_creates_dir_and_is_idempotent(tmp_path):
    db = tmp_path / "nested" / "dir" / ".strata" / "strata.db"
    st = SqliteStorage(db)
    st.init()
    st.init()  # no-op
    assert db.exists()
    h = st.health()
    assert h["ok"] and h["schema_version"] == S.SCHEMA_VERSION and h["fts5"] is True
    st.close()


def test_methods_require_init(tmp_path):
    st = SqliteStorage(tmp_path / "x.db")
    assert st.health() == {"ok": False, "initialised": False}
    with pytest.raises(RuntimeError):
        st.list_projects()


def test_sanitize_text():
    assert sanitize_text("a\x00b\x07c") == "abc"
    assert sanitize_text("foo   bar\t\tbaz") == "foo bar baz"
    assert sanitize_text("line1\n\n\n\n\nline2") == "line1\n\nline2"
    assert sanitize_text("  \n  ") is None
    assert sanitize_text(None) is None


# --------------------------------------------------------------------------- #
# projects & phases
# --------------------------------------------------------------------------- #
def test_create_project_seeds_phases_and_rejects_dup(store):
    p = Project.create("P1")
    store.create_project(p)
    phases = store.get_phases(p.id)
    assert len(phases) == 10
    assert phases[0]["status"] == "in_progress" and phases[0]["started_at"]
    assert all(ph["status"] == "pending" for ph in phases[1:])
    assert store.get_project(p.id).name == "P1"
    assert [x.id for x in store.list_projects()] == [p.id]
    with pytest.raises(ValueError):
        store.create_project(Project(id=p.id, name="dup"))


def test_set_phase_transitions(store, project):
    store.set_phase(project.id, 1, "in_progress", notes="working")
    ph1 = next(p for p in store.get_phases(project.id) if p["phase_number"] == 1)
    assert ph1["status"] == "in_progress" and ph1["started_at"] and ph1["notes"] == "working"
    store.set_phase(project.id, 1, "completed")
    ph1 = next(p for p in store.get_phases(project.id) if p["phase_number"] == 1)
    assert ph1["status"] == "completed" and ph1["completed_at"]
    # re-opening clears completed_at, keeps notes (COALESCE(NULL, notes))
    store.set_phase(project.id, 1, "in_progress")
    ph1 = next(p for p in store.get_phases(project.id) if p["phase_number"] == 1)
    assert ph1["completed_at"] is None and ph1["notes"] == "working"
    with pytest.raises(ValueError):
        store.set_phase(project.id, 99, "pending")
    with pytest.raises(ValueError):
        store.set_phase(project.id, 1, "weird")
    with pytest.raises(ValueError):
        store.set_phase("no-such-project", 1, "pending")


# --------------------------------------------------------------------------- #
# papers, dedupe, analysis, context
# --------------------------------------------------------------------------- #
def test_save_paper_dedup_and_merge(store, project):
    p1 = Paper.create(
        "Attention Is All You Need",
        arxiv_id="2401.12345",
        authors=["A. Vaswani"],
        year=2017,
        raw_text="We propose the Transformer.",
        source="arxiv",
    )
    saved = store.save_paper(p1, project_id=project.id)
    assert store.find_paper_by_dedupe_key("arxiv:2401.12345").id == saved.id

    # re-ingest the same work (versioned arxiv URL) with extra metadata, no raw_text
    p2 = Paper.create(
        "Attention Is All You Need",
        arxiv_id="https://arxiv.org/abs/2401.12345v3",
        doi="10.5555/abc",
        source="arxiv",
    )
    saved2 = store.save_paper(p2, project_id=project.id)
    assert saved2.id == saved.id  # same row
    assert saved2.doi == "10.5555/abc"  # new metadata merged in
    assert saved2.raw_text == "We propose the Transformer."  # old non-null kept
    assert len(store.list_papers(project.id)) == 1
    # the row's dedupe key was upgraded to the now-known DOI
    assert store.find_paper_by_dedupe_key("doi:10.5555/abc").id == saved.id
    assert store.get_paper(saved.id) is not None

    # a different paper
    other = Paper.create("Some Other Paper", arxiv_id="2009.06732", source="arxiv")
    store.save_paper(other, project_id=project.id)
    assert len(store.list_papers(project.id)) == 2


def test_get_paper_is_project_scoped(store):
    a = Project.create("A")
    b = Project.create("B")
    store.create_project(a)
    store.create_project(b)
    paper = store.save_paper(Paper.create("Shared?", arxiv_id="2401.00001"), project_id=a.id)
    assert store.get_paper(paper.id, project_id=a.id) is not None
    assert store.get_paper(paper.id, project_id=b.id) is None
    assert store.get_paper(paper.id) is not None  # unscoped lookup works
    assert store.list_papers(b.id) == []


def test_save_paper_analysis_keeps_other_round(store, project):
    paper = store.save_paper(Paper.create("T", arxiv_id="2401.00002"), project_id=project.id)
    store.save_paper_analysis(
        PaperAnalysis(
            paper_id=paper.id,
            round1=Round1Analysis(bullets=["x"], relevance_score=8),
            model_used="haiku",
        )
    )
    store.save_paper_analysis(
        PaperAnalysis(
            paper_id=paper.id,
            round2=Round2Analysis(methodology="self-attention"),
            model_used="haiku",
        )
    )
    got = store.get_paper_analysis(paper.id)
    assert got.round1 is not None and got.round1.relevance_score == 8.0
    assert got.round2.methodology == "self-attention"
    assert got.model_used == "haiku"
    with pytest.raises(ValueError):
        store.save_paper_analysis(PaperAnalysis(paper_id="ghost", round1=Round1Analysis()))


def test_save_context_upserts_and_keeps_added_at(store, project):
    paper = store.save_paper(Paper.create("T", arxiv_id="2401.00003"), project_id=project.id)
    before = store.get_context(paper.id, project.id)  # created by save_paper(project_id=...)
    assert before is not None and before.context_analysis == {}
    store.save_context(
        PaperProject(
            paper_id=paper.id, project_id=project.id, context_analysis={"k": "v"}, relevance_score=7
        )
    )
    after = store.get_context(paper.id, project.id)
    assert after.context_analysis == {"k": "v"} and after.relevance_score == 7.0
    assert after.added_at == before.added_at
    with pytest.raises(ValueError):
        store.save_context(PaperProject(paper_id="ghost", project_id=project.id))


# --------------------------------------------------------------------------- #
# FTS search
# --------------------------------------------------------------------------- #
def test_search(store, project):
    p1 = store.save_paper(
        Paper.create(
            "Attention Is All You Need",
            arxiv_id="2401.10001",
            abstract="A new architecture based solely on attention mechanisms.",
            raw_text="We propose the Transformer, dispensing with recurrence and convolutions.",
        ),
        project_id=project.id,
    )
    p2 = store.save_paper(
        Paper.create(
            "Deep Residual Learning",
            arxiv_id="2401.10002",
            raw_text="Residual connections ease training of very deep networks.",
        ),
        project_id=project.id,
    )
    store.save_paper_analysis(
        PaperAnalysis(
            paper_id=p1.id,
            round1=Round1Analysis(
                bullets=["transformer beats RNN"], summary_es="Propone el transformer"
            ),
        )
    )
    hits = store.search(project.id, "attention transformer")
    assert hits and hits[0]["paper_id"] == p1.id and hits[0]["matched_in"] in {"paper", "analysis"}
    assert "title" in hits[0] and hits[0]["title"].startswith("Attention")
    # residual term -> p2 only
    res = store.search(project.id, "residual networks")
    assert [h["paper_id"] for h in res] == [p2.id]
    # empty / whitespace / operator-ish queries never blow up
    assert store.search(project.id, "   ") == []
    assert store.search(project.id, '"*OR(') == []
    # project scoping
    other = Project.create("Other")
    store.create_project(other)
    assert store.search(other.id, "attention") == []
    # limit honoured
    assert len(store.search(project.id, "the", limit=1)) <= 1


# --------------------------------------------------------------------------- #
# ingest queue
# --------------------------------------------------------------------------- #
def test_queue_dedups_active_and_dequeues_atomically(store, project):
    items = store.queue_papers(
        project.id,
        [
            "https://arxiv.org/abs/2401.0001",
            "https://arxiv.org/abs/2401.0001?utm_source=x",  # dup after normalisation
            "  ",  # skipped
            "https://arxiv.org/abs/2401.0002",
        ],
        hint="arxiv",
    )
    assert {i.url for i in items} == {
        "https://arxiv.org/abs/2401.0001",
        "https://arxiv.org/abs/2401.0002",
    }
    assert store.queue_status(project.id) == {"pending": 2, "processing": 0, "done": 0, "failed": 0}
    # re-queue the same active url -> still just the existing item
    again = store.queue_papers(project.id, ["https://arxiv.org/abs/2401.0001"])
    assert len(again) == 1
    assert store.queue_status(project.id)["pending"] == 2

    a = store.dequeue_paper("w1")
    b = store.dequeue_paper("w2")
    assert a.id != b.id and a.attempts == 1 and b.attempts == 1
    assert {a.url, b.url} == {i.url for i in items}
    assert store.dequeue_paper("w3") is None  # empty
    assert store.queue_status(project.id)["processing"] == 2

    store.mark_ingested(a.id)
    assert store.queue_status(project.id) == {"pending": 0, "processing": 1, "done": 1, "failed": 0}
    with pytest.raises(ValueError):
        store.mark_ingested(999999)


def test_mark_failed_retries_then_gives_up(store, project):
    store.queue_papers(project.id, ["https://bad.example/404"])
    for _ in range(2):
        item = store.dequeue_paper("w")
        store.mark_failed(item.id, "HTTP 404")
        assert store.queue_status(project.id)["pending"] == 1  # back to pending
    item = store.dequeue_paper("w")
    assert item.attempts == 3
    store.mark_failed(item.id, "HTTP 404", raw="<html>not found</html>")
    assert store.queue_status(project.id) == {"pending": 0, "processing": 0, "done": 0, "failed": 1}
    with pytest.raises(ValueError):
        store.mark_failed(424242, "nope")


def test_stale_processing_items_are_reclaimed(store, project):
    store.queue_papers(project.id, ["https://arxiv.org/abs/2401.7777"])
    item = store.dequeue_paper("dead-worker")
    # forge a stale processed_at directly
    store._conn().execute(
        "UPDATE ingest_queue SET processed_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (item.id,),
    )
    again = store.dequeue_paper("fresh-worker")
    assert again.id == item.id and again.attempts == 2 and again.worker_id == "fresh-worker"


# --------------------------------------------------------------------------- #
# versioned artefacts
# --------------------------------------------------------------------------- #
def test_gap_and_literature_review_versions(store, project):
    g1 = store.save_gap(project.id, "gap one")
    g2 = store.save_gap(project.id, "gap two")
    assert (g1.version, g2.version) == (1, 2)
    assert store.get_gap(project.id).content_md == "gap two"
    assert store.get_gap(project.id, 1).content_md == "gap one"
    assert store.get_gap(project.id, 99) is None
    assert store.get_gap("ghost") is None
    lr = store.save_literature_review(project.id, "state of the art")
    assert (
        lr.version == 1 and store.get_literature_review(project.id).content_md == "state of the art"
    )
    with pytest.raises(ValueError):
        store.save_gap(project.id, "   ")
    with pytest.raises(ValueError):
        store.save_gap("ghost", "x")


def test_draft_versions_are_per_section(store, project):
    d1 = store.save_draft(project.id, "intro a", section="introduction")
    d2 = store.save_draft(project.id, "intro b", section="Introduction")  # normalised
    full1 = store.save_draft(project.id, "whole paper")
    full2 = store.save_draft(project.id, "whole paper v2")
    assert (d1.version, d2.version) == (1, 2)
    assert (full1.version, full2.version) == (1, 2)
    assert store.get_latest_draft(project.id, "introduction").content_md == "intro b"
    assert store.get_latest_draft(project.id).content_md == "whole paper v2"
    assert store.get_latest_draft(project.id, "results") is None
    with pytest.raises(ValueError):
        store.save_draft(project.id, "x", section="bogus-section")
    with pytest.raises(ValueError):
        store.save_draft(project.id, "")


# --------------------------------------------------------------------------- #
# candidates & repo snapshot
# --------------------------------------------------------------------------- #
def test_candidates_lifecycle(store, project):
    c1 = Candidate.create(
        project.id,
        "Efficient Transformers Survey",
        arxiv_id="2009.06732",
        relevance_score=8,
        source="arxiv",
    )
    c2 = Candidate.create(
        project.id, "Long Range Arena", arxiv_id="2011.04006", relevance_score=6, source="arxiv"
    )
    store.save_candidates(project.id, [c1, c2])
    listed = store.list_candidates(project.id)
    assert [c.title for c in listed] == [
        "Efficient Transformers Survey",
        "Long Range Arena",
    ]  # by score desc
    assert all(c.status.value == "pending" for c in listed)
    # idempotent re-save with updated score
    c1b = Candidate(
        id=c1.id, project_id=project.id, title=c1.title, relevance_score=9.5, source="arxiv"
    )
    store.save_candidates(project.id, [c1b])
    assert store.list_candidates(project.id)[0].relevance_score == 9.5

    paper = store.save_paper(
        Paper.create("Efficient Transformers Survey", arxiv_id="2009.06732"), project_id=project.id
    )
    store.set_candidate_status(c1.id, "approved", paper_id=paper.id)
    approved = store.list_candidates(project.id, "approved")
    assert len(approved) == 1 and approved[0].paper_id == paper.id
    store.set_candidate_status(c2.id, "rejected")
    assert {c.status.value for c in store.list_candidates(project.id)} == {"approved", "rejected"}
    with pytest.raises(ValueError):
        store.set_candidate_status("ghost", "approved")
    with pytest.raises(ValueError):
        store.set_candidate_status(c1.id, "nonsense")
    with pytest.raises(ValueError):
        store.set_candidate_status(c1.id, "approved", paper_id="ghost-paper")
    with pytest.raises(ValueError):
        store.list_candidates(project.id, "nonsense")


def test_repo_snapshot_upsert(store, project):
    assert store.get_repo_snapshot(project.id) is None
    store.save_repo_snapshot(
        RepoSnapshot(project_id=project.id, repo_url="https://github.com/x/y", layers={"l1": 1})
    )
    snap = store.get_repo_snapshot(project.id)
    assert snap.repo_url == "https://github.com/x/y" and snap.layers == {"l1": 1}
    store.save_repo_snapshot(
        RepoSnapshot(project_id=project.id, repo_url="https://github.com/x/z", layers={"l2": 2})
    )
    snap = store.get_repo_snapshot(project.id)
    assert snap.repo_url == "https://github.com/x/z" and snap.layers == {"l2": 2}
    with pytest.raises(ValueError):
        store.save_repo_snapshot(RepoSnapshot(project_id="ghost", repo_url="https://x"))


def test_health_reports_counts(store, project):
    store.save_paper(Paper.create("P", arxiv_id="2401.55555"), project_id=project.id)
    store.queue_papers(project.id, ["https://arxiv.org/abs/2401.6"])
    h = store.health()
    assert h["ok"] and h["counts"]["projects"] == 1 and h["counts"]["papers"] == 1
    assert h["queue"]["pending"] == 1
