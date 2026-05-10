#!/usr/bin/env python3
"""migrate_supabase.py -- one-shot migration of the old Strata data from
Supabase (Postgres) into per-project ``.strata/strata.db`` SQLite databases.

Exports ``projects``, ``papers`` (with round1/round2/raw_text), ``paper_project``
(context_analysis, relevance_score), ``drafts``, ``gap_analyses`` and
``candidates``; for each project creates ``<dest>/<project-slug>/.strata/strata.db``
and loads its papers/analyses/gaps/drafts/candidates. Idempotent (upsert by id).
Papers whose ``round2_analysis`` is NULL (failed in the original ingest) are
flagged so a later ``/strata`` run re-analyses them.

Reads Supabase credentials from the environment (``SUPABASE_URL``,
``SUPABASE_KEY``) -- see ``.env.example``.

SCAFFOLD: implemented in phase 7 (v1 scope).

Usage:
    python scripts/migrate_supabase.py --dest ~/RoadToDevOps
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Strata data Supabase -> SQLite (.strata/)."
    )
    parser.add_argument("--dest", default=".", help="parent directory for the per-project dirs")
    parser.add_argument("--dry-run", action="store_true", help="report what would be migrated")
    parser.parse_args(argv)
    print("migrate_supabase.py: scaffold only -- implemented in phase 7.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
