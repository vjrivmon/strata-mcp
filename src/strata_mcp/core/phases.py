"""The ``/strata`` research workflow phases (mirrors the apex phase pattern).

Each research project tracks its progress through these phases in the ``phases``
table. This module is the single source of truth for phase numbers and names —
look-ups, the seed rows for a new project, and the small "what comes next"
helper the ``/strata`` command uses.
"""

from __future__ import annotations

from dataclasses import dataclass

from strata_mcp.core.entities import ResearchPhase

VALID_STATUSES: tuple[str, ...] = ("pending", "in_progress", "completed", "skipped")


@dataclass(frozen=True)
class PhaseDef:
    number: int
    key: str
    name: str
    summary: str


# Ordered catalogue of research phases. Keep in sync with commands/strata.md.
RESEARCH_PHASES: tuple[PhaseDef, ...] = (
    PhaseDef(
        0,
        "setup",
        "Setup",
        "Define the project: research question, domain, template, optional repo_url. Create .strata/.",
    ),
    PhaseDef(
        1,
        "library",
        "Library",
        "Queue papers (URL/DOI/PDF/local). Haiku subagents drain the queue: fetch text -> round 1 -> round 2 -> save.",
    ),
    PhaseDef(
        2,
        "relevance",
        "Relevance",
        "Per paper x project: context analysis (contribution, gaps covered/not, score).",
    ),
    PhaseDef(
        3,
        "gap",
        "Gap analysis",
        "Cross all round-2 analyses + context + research question (+ repo) -> structured gap.",
    ),
    PhaseDef(
        4,
        "scout",
        "Scout",
        "Design 6 queries -> search arXiv + Semantic Scholar -> rank 0-10 -> save candidates. Approve -> re-queue to Library.",
    ),
    PhaseDef(
        5,
        "literature_review",
        "Literature review",
        "Write related work / state of the art from the library.",
    ),
    PhaseDef(
        6,
        "draft",
        "Draft",
        "Write the paper section by section using gap + library + repo. Cite only library papers.",
    ),
    PhaseDef(
        7,
        "qa",
        "QA",
        "Verify every citation exists in the library, no hallucination, reference format matches the template.",
    ),
    PhaseDef(8, "export", "Export", "Generate the final .tex/.md with bibliography per template."),
    PhaseDef(9, "iteration", "Iteration", "Add papers, regenerate sections, re-scout."),
)

_BY_NUMBER: dict[int, PhaseDef] = {p.number: p for p in RESEARCH_PHASES}
_BY_KEY: dict[str, PhaseDef] = {p.key: p for p in RESEARCH_PHASES}
FIRST_PHASE: int = RESEARCH_PHASES[0].number
LAST_PHASE: int = RESEARCH_PHASES[-1].number


def phase_def(number: int) -> PhaseDef:
    """Look up a phase definition by number, or raise ``ValueError``."""
    try:
        return _BY_NUMBER[number]
    except KeyError as exc:
        valid = ", ".join(str(p.number) for p in RESEARCH_PHASES)
        raise ValueError(f"unknown phase {number!r}; valid phases: {valid}") from exc


def phase_by_key(key: str) -> PhaseDef:
    """Look up a phase definition by its short key (``"setup"``, ``"draft"`` …),
    or raise ``ValueError``."""
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        valid = ", ".join(p.key for p in RESEARCH_PHASES)
        raise ValueError(f"unknown phase key {key!r}; valid keys: {valid}") from exc


def is_valid_phase(number: int) -> bool:
    return number in _BY_NUMBER


def is_valid_status(status: str) -> bool:
    return status in VALID_STATUSES


def next_phase(number: int) -> PhaseDef | None:
    """The phase after ``number`` in workflow order, or ``None`` past the end.
    Raises ``ValueError`` for an unknown ``number``."""
    phase_def(number)  # validates
    return _BY_NUMBER.get(number + 1)


def initial_phases(project_id: str) -> list[dict]:
    """Rows to seed the ``phases`` table when a project is created (all
    ``pending``, except ``Setup`` which starts ``in_progress``)."""
    return [
        {
            "project_id": project_id,
            "phase_number": p.number,
            "name": p.name,
            "status": "in_progress" if p.number == ResearchPhase.SETUP else "pending",
        }
        for p in RESEARCH_PHASES
    ]
