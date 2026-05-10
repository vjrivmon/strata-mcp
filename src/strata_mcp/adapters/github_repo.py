"""GitHub repo source: layered introspection of a project's code repository.

Builds a :class:`RepoSnapshot` with four layers — (1) README, folder tree,
PENDING/TODO files; (2) agents (classes, responsibilities, prompts); (3)
benchmarks and metrics (``benchmark*`` files, result JSONs, E2E tests);
(4) datasets, configs, comparison results — as raw structured data. Claude Code
summarises it into the gap/draft prompt; no LLM here.

Auth: uses ``GITHUB_TOKEN`` from the environment, or the ``gh`` CLI's token if
available. (``~/.config/github_token`` does not exist on this machine.) If a
private repo can't be read, degrades to an empty snapshot rather than raising.

Scaffold note: signatures only; implementation in phase 7 (v1 scope).
"""

from __future__ import annotations

from strata_mcp.core.entities import RepoSnapshot
from strata_mcp.core.ports import IRepoSource


class GithubRepoSource(IRepoSource):
    def scan(self, repo_url_or_path: str) -> RepoSnapshot:
        raise NotImplementedError
