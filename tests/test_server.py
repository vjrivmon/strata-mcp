"""Tests for the MCP tool layer (``strata_mcp.server``). The tools are exercised
in-process against a temp-file DB bound via :func:`server.bind_storage`; the
FastMCP stdio transport itself is not started here."""

from __future__ import annotations

import pytest

from strata_mcp import server as srv
from strata_mcp.adapters.sqlite_storage import SqliteStorage


@pytest.fixture
def store(tmp_path):
    st = SqliteStorage(tmp_path / ".strata" / "strata.db")
    srv.bind_storage(st)
    yield st
    srv.bind_storage(None)


@pytest.fixture
def project(store):
    return srv.strata_create_project(
        "Transformers Thesis", research_question="Why attention?", template="lncs"
    )


def test_all_mvp_tools_are_registered():
    import asyncio

    names = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    expected = {
        "strata_health",
        "strata_get_status",
        "strata_init_project",
        "strata_create_project",
        "strata_list_projects",
        "strata_get_project",
        "strata_set_phase",
        "strata_list_phases",
        "strata_queue_papers",
        "strata_queue_status",
        "strata_dequeue_paper",
        "strata_mark_ingested",
        "strata_mark_failed",
        "strata_fetch_paper_text",
        "strata_fetch_and_stage",
        "strata_save_paper",
        "strata_get_paper",
        "strata_list_papers",
        "strata_save_paper_analysis",
        "strata_get_paper_analysis",
        "strata_save_context",
        "strata_get_context",
        "strata_search",
        "strata_save_gap",
        "strata_get_gap",
        "strata_save_literature_review",
        "strata_get_literature_review",
        "strata_save_draft",
        "strata_get_latest_draft",
        "strata_search_arxiv",
        "strata_save_candidates",
        "strata_list_candidates",
        "strata_approve_candidate",
        "strata_reject_candidate",
    }
    assert expected <= names
    # every tool has a non-empty description (the LLM relies on it)
    for t in asyncio.run(srv.mcp.list_tools()):
        assert t.description and t.description.strip()


def test_health_and_status(store, project):
    h = srv.strata_health()
    assert h["ok"] and "version" in h
    assert srv.strata_init_project()["ok"]
    st = srv.strata_get_status()
    assert any(p["id"] == project["id"] for p in st["projects"])
    st = srv.strata_get_status(project["id"])
    assert st["project"]["id"] == project["id"] and st["paper_count"] == 0
    assert len(st["phases"]) == 10
    with pytest.raises(ValueError):
        srv.strata_get_status("ghost")
    assert len(srv.strata_list_phases()) == 10


def test_project_and_phase_tools(store):
    p = srv.strata_create_project("P", template="IEEE")
    assert p["template"] == "ieee" and p["id"]
    assert {x["id"] for x in srv.strata_list_projects()} == {p["id"]}
    got = srv.strata_get_project(p["id"])
    assert got["id"] == p["id"] and len(got["phases"]) == 10
    out = srv.strata_set_phase(p["id"], 1, "in_progress", notes="draining")
    ph1 = next(x for x in out["phases"] if x["phase_number"] == 1)
    assert ph1["status"] == "in_progress" and ph1["notes"] == "draining"
    with pytest.raises(ValueError):
        srv.strata_create_project("bad", template="springer")
    with pytest.raises(ValueError):
        srv.strata_set_phase(p["id"], 1, "bogus")


def test_queue_ingest_save_search_flow(store, project):
    pid = project["id"]
    q = srv.strata_queue_papers(
        pid,
        [
            "https://arxiv.org/abs/2401.0001",
            "https://arxiv.org/abs/2401.0001?utm_source=x",
            " ",
            "https://arxiv.org/abs/2401.0002",
        ],
        hint="arxiv",
    )
    assert len(q["queued"]) == 2 and q["queue"]["pending"] == 2
    assert srv.strata_queue_status(pid)["pending"] == 2

    item = srv.strata_dequeue_paper("w1")
    assert (
        item["status"] == "processing"
        and item["attempts"] == 1
        and item["url"].endswith(("0001", "0002"))
    )
    item2 = srv.strata_dequeue_paper("w2")
    assert item2["id"] != item["id"]
    assert srv.strata_dequeue_paper("w3") is None

    saved = srv.strata_save_paper(
        "Attention Is All You Need",
        project_id=pid,
        arxiv_id="2401.0001",
        authors=["A. Vaswani", "N. Shazeer"],
        year=2017,
        abstract="An architecture based solely on attention.",
        raw_text="We propose the Transformer, dispensing with recurrence.",
        source="arxiv",
    )
    assert saved["source"] == "arxiv" and saved["arxiv_id"] == "2401.0001"
    srv.strata_save_paper_analysis(
        saved["id"],
        round1={
            "bullets": ["transformer beats rnn"],
            "relevance_score": 9.5,
            "worth_reading": True,
            "summary_es": "Propone el transformer",
        },
        model_used="haiku",
    )
    srv.strata_save_paper_analysis(
        saved["id"],
        round2={
            "methodology": "scaled dot-product attention",
            "key_contributions": "no recurrence",
        },
        model_used="haiku",
    )
    srv.strata_mark_ingested(item["id"])
    assert srv.strata_queue_status(pid)["done"] == 1
    srv.strata_mark_failed(item2["id"], "HTTP 404")
    assert srv.strata_queue_status(pid)["pending"] == 1  # retried

    paper = srv.strata_get_paper(saved["id"], project_id=pid)
    assert paper["analysis"]["round1"]["relevance_score"] == 9.5
    assert paper["analysis"]["round2"]["methodology"] == "scaled dot-product attention"
    assert paper["context"] is not None  # link created by save_paper(project_id=)
    an = srv.strata_get_paper_analysis(saved["id"])
    assert an["model_used"] == "haiku"
    listed = srv.strata_list_papers(pid, include_analysis=True)
    assert len(listed) == 1 and listed[0]["analysis"] is not None

    hits = srv.strata_search(pid, "attention transformer")
    assert hits and hits[0]["paper_id"] == saved["id"]
    assert srv.strata_search(pid, "   ") == []

    srv.strata_save_context(
        saved["id"], pid, {"contribution_to_project": "foundational"}, relevance_score=9.0
    )
    ctx = srv.strata_get_context(saved["id"], pid)
    assert (
        ctx["context_analysis"]["contribution_to_project"] == "foundational"
        and ctx["relevance_score"] == 9.0
    )

    with pytest.raises(ValueError):
        srv.strata_get_paper("ghost", project_id=pid)
    with pytest.raises(ValueError):
        srv.strata_save_paper_analysis(saved["id"])  # neither round


def test_gap_draft_literature_tools(store, project):
    pid = project["id"]
    assert srv.strata_get_gap(pid) is None
    g1 = srv.strata_save_gap(pid, "# Gap\nMissing efficiency analysis.")
    g2 = srv.strata_save_gap(pid, "# Gap v2")
    assert (g1["version"], g2["version"]) == (1, 2)
    assert srv.strata_get_gap(pid)["version"] == 2 and srv.strata_get_gap(pid, 1)["version"] == 1
    lr = srv.strata_save_literature_review(pid, "State of the art ...")
    assert lr["version"] == 1 and srv.strata_get_literature_review(pid)["content_md"].startswith(
        "State"
    )
    d1 = srv.strata_save_draft(pid, "Intro a", section="introduction")
    d2 = srv.strata_save_draft(pid, "Intro b", section="Introduction")
    full = srv.strata_save_draft(pid, "Full paper")
    assert (d1["version"], d2["version"], full["version"]) == (1, 2, 1)
    assert srv.strata_get_latest_draft(pid, "introduction")["content_md"] == "Intro b"
    assert srv.strata_get_latest_draft(pid)["content_md"] == "Full paper"
    assert srv.strata_get_latest_draft(pid, "results") is None


def test_scout_candidate_tools(store, project):
    pid = project["id"]
    cs = srv.strata_save_candidates(
        pid,
        [
            {
                "title": "Efficient Transformers Survey",
                "arxiv_id": "2009.06732",
                "relevance_score": 8,
                "relevance_reason": "directly on topic",
                "source": "arxiv",
            },
            {"title": "Long Range Arena", "arxiv_id": "2011.04006", "relevance_score": 6},
        ],
    )
    assert [c["title"] for c in cs] == ["Efficient Transformers Survey", "Long Range Arena"]
    assert cs[0]["source"] == "arxiv" and cs[1]["source"] == "unknown"
    assert all(c["status"] == "pending" for c in cs)
    assert [c["status"] for c in srv.strata_list_candidates(pid, "pending")] == [
        "pending",
        "pending",
    ]

    out = srv.strata_approve_candidate(cs[0]["id"], pid)
    assert len(out["queued"]) == 1 and out["queue"]["pending"] == 1
    srv.strata_reject_candidate(cs[1]["id"])
    statuses = {c["title"]: c["status"] for c in srv.strata_list_candidates(pid)}
    assert statuses == {"Efficient Transformers Survey": "approved", "Long Range Arena": "rejected"}

    with pytest.raises(ValueError):
        srv.strata_save_candidates(pid, [{"abstract": "no title"}])
    with pytest.raises(ValueError):
        srv.strata_approve_candidate("ghost", pid)


def test_fetch_paper_text_dispatch():
    with pytest.raises(ValueError):
        srv.strata_fetch_paper_text("")
    with pytest.raises(ValueError):
        # a plain web page: no source in this MVP can handle it (arXiv/PDF only)
        srv.strata_fetch_paper_text("https://example.com/some-web-page")


def test_fetch_and_stage_round_trips_raw_text_via_staging(
    store, project, tmp_path, sample_pdf_bytes
):
    pid = project["id"]
    pdf_path = tmp_path / "great_paper.pdf"
    pdf_path.write_bytes(sample_pdf_bytes)
    srv.strata_queue_papers(pid, [str(pdf_path)])
    item = srv.strata_dequeue_paper("w1")

    staged = srv.strata_fetch_and_stage(item["id"])
    # the megabyte-sized full text stays server-side; the subagent only sees the truncated one
    assert "raw_text" not in staged
    assert staged["raw_text_truncated"] and staged["raw_text_staged"] is True
    assert staged["queue_id"] == item["id"] and staged["source"] == "pdf" and staged["title"]
    assert store.get_staged_raw_text(item["id"])  # parked on the queue row

    saved = srv.strata_save_paper(
        staged["title"],
        project_id=pid,
        url=staged.get("url"),
        source="pdf",
        from_queue_id=item["id"],
    )
    assert saved["raw_text"] and "abstract" in saved["raw_text"].lower()

    srv.strata_mark_ingested(item["id"])
    assert srv.strata_queue_status(pid)["done"] == 1
    assert store.get_staged_raw_text(item["id"]) is None  # cleared once the paper is saved

    # an explicit raw_text always wins over the staged one
    s2 = srv.strata_save_paper(
        "Another",
        project_id=pid,
        url="https://example.org/p",
        raw_text="explicit text here",
        from_queue_id=item["id"],
    )
    assert s2["raw_text"] == "explicit text here"

    with pytest.raises(ValueError):
        srv.strata_fetch_and_stage(999_999)


def test_mark_failed_permanent_does_not_retry(store, project):
    pid = project["id"]
    srv.strata_queue_papers(pid, ["https://arxiv.org/abs/9999.99999"], hint="arxiv")
    item = srv.strata_dequeue_paper("w1")
    srv.strata_mark_failed(item["id"], "arXiv paper not found: 9999.99999", permanent=True)
    qs = srv.strata_queue_status(pid)
    assert qs["failed"] == 1 and qs["pending"] == 0  # not retried
    assert store.get_queue_item(item["id"]).status.value == "failed"
    # a transient failure on a fresh item, by contrast, goes back to pending
    srv.strata_queue_papers(pid, ["https://arxiv.org/abs/2401.0002"], hint="arxiv")
    other = srv.strata_dequeue_paper("w1")
    srv.strata_mark_failed(other["id"], "HTTP 503")
    assert srv.strata_queue_status(pid)["pending"] == 1
