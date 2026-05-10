"""Unit tests for :mod:`strata_mcp.core.dedupe` — normalisers, the hierarchical
dedupe key, and the softer ``same_paper`` comparison."""

from __future__ import annotations

import pytest

from strata_mcp.core import dedupe


# --------------------------------------------------------------------------- #
# normalisers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1000/ABC", "10.1000/abc"),
        ("http://dx.doi.org/10.5/X", "10.5/x"),
        ("doi:10.5/x", "10.5/x"),
        ("  10.1/Y/  ", "10.1/y"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_doi(raw, expected):
    assert dedupe.normalize_doi(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://arxiv.org/abs/2401.12345v3", "2401.12345"),
        ("https://arxiv.org/pdf/2401.12345.pdf", "2401.12345"),
        ("arXiv:2401.12345", "2401.12345"),
        ("2401.12345", "2401.12345"),
        ("hep-th/9901001v2", "hep-th/9901001"),
        ("https://arxiv.org/abs/2401.12345?context=cs", "2401.12345"),
        ("not-an-id", None),
        ("arXiv:", None),
        ("v3", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_arxiv_id(raw, expected):
    assert dedupe.normalize_arxiv_id(raw) == expected


def test_normalize_url_strips_fragment_tracking_and_trailing_slash():
    base = "https://example.com/p/1"
    assert dedupe.normalize_url(base + "/?utm_source=x&id=7#frag") == base + "?id=7"
    assert dedupe.normalize_url("HTTPS://Example.COM/p/1/") == base
    # not an absolute URL -> returned stripped, not dropped
    assert dedupe.normalize_url("  example.com/x  ") == "example.com/x"
    assert dedupe.normalize_url("   ") is None
    assert dedupe.normalize_url(None) is None


def test_normalize_title_de_latex_and_punctuation():
    assert dedupe.normalize_title("  The  {\\em Foo}: A Bar! ") == "the em foo a bar"
    assert dedupe.normalize_title(r"Energy $E=mc^2$ revisited") == "energy revisited"
    assert dedupe.normalize_title("") == ""
    assert dedupe.normalize_title(None) == ""


@pytest.mark.parametrize(
    ("authors", "expected"),
    [
        (["Vicente Rivas Monferrer", "Juan M. Alberola"], "monferrer"),
        (["Rivas Monferrer, Vicente"], "monferrer"),
        (["  ", "Ada Lovelace"], "lovelace"),
        (["Erdős, Paul"], "erdos"),
        (["  ", ""], None),
        ([",", ""], None),
        ([], None),
        (None, None),
    ],
)
def test_first_author_surname(authors, expected):
    assert dedupe.first_author_surname(authors) == expected


# --------------------------------------------------------------------------- #
# dedupe_key
# --------------------------------------------------------------------------- #
def test_dedupe_key_hierarchy():
    assert (
        dedupe.dedupe_key(doi="10.1/x", arxiv_id="2401.00001", url="u", title="t") == "doi:10.1/x"
    )
    assert (
        dedupe.dedupe_key(arxiv_id="https://arxiv.org/abs/2401.00001v2", title="t")
        == "arxiv:2401.00001"
    )
    assert dedupe.dedupe_key(url="https://x.io/p#a", title="t") == "url:https://x.io/p"
    assert dedupe.dedupe_key(title="  Hello   World  ") == "title:hello world"


def test_dedupe_key_skips_unusable_identifiers():
    # arxiv_id that doesn't parse falls through to url, then title
    assert dedupe.dedupe_key(arxiv_id="garbage", title="My Paper") == "title:my paper"
    assert dedupe.dedupe_key(doi="", arxiv_id=None, url="  ", title="T") == "title:t"


def test_dedupe_key_needs_something():
    with pytest.raises(ValueError):
        dedupe.dedupe_key()
    with pytest.raises(ValueError):
        dedupe.dedupe_key(doi=None, arxiv_id="", url=None, title="   ")


def test_dedupe_key_for_accepts_dict_and_object():
    d = {"doi": "10.9/Z", "title": "x"}
    assert dedupe.dedupe_key_for(d) == "doi:10.9/z"

    class P:
        doi = None
        arxiv_id = "2402.55555"
        url = None
        title = None

    assert dedupe.dedupe_key_for(P()) == "arxiv:2402.55555"


# --------------------------------------------------------------------------- #
# same_paper
# --------------------------------------------------------------------------- #
def test_same_paper_strong_identifiers_decisive():
    assert dedupe.same_paper(
        {"doi": "https://doi.org/10.1/X"}, {"doi": "10.1/x", "title": "totally different"}
    )
    assert not dedupe.same_paper({"doi": "10.1/a"}, {"doi": "10.1/b"})
    assert dedupe.same_paper({"arxiv_id": "2401.01234v1"}, {"arxiv_id": "2401.01234v9"})
    assert not dedupe.same_paper({"arxiv_id": "2401.00001"}, {"arxiv_id": "2401.00002"})


def test_same_paper_url_match_confirms_but_mismatch_does_not():
    assert dedupe.same_paper({"url": "https://a.io/p/"}, {"url": "https://a.io/p#frag"})
    # different urls but identical title + same first author -> still the same work
    assert dedupe.same_paper(
        {"url": "https://arxiv.org/abs/2401.1", "title": "Deep Foo", "authors": ["A. Smith"]},
        {"url": "https://springer.com/article/xyz", "title": "Deep Foo", "authors": ["Smith, A."]},
    )


def test_same_paper_title_fallback_with_author_year_tiebreak():
    a = {"title": "On Bar", "authors": ["Jane Doe"], "year": 2020}
    assert dedupe.same_paper(a, {"title": "on   bar", "authors": ["Doe, Jane"], "year": 2020})
    # same title, different year -> homonyms
    assert not dedupe.same_paper(a, {"title": "On Bar", "authors": ["Jane Doe"], "year": 1998})
    # same title, different first author -> not the same
    assert not dedupe.same_paper(a, {"title": "On Bar", "authors": ["Other Person"]})
    # same title, nothing contradicts (no authors/years to compare) -> assume same
    assert dedupe.same_paper({"title": "On Bar"}, {"title": "ON BAR"})
    # different titles, no strong id -> not the same
    assert not dedupe.same_paper({"title": "On Bar"}, {"title": "On Baz"})
    # no usable title at all -> not the same
    assert not dedupe.same_paper({"title": ""}, {"title": ""})
