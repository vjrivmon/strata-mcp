"""Code-repository source: layered introspection of a project's repo.

Builds a :class:`~strata_mcp.core.entities.RepoSnapshot` with four layers of
*raw structured data* (no LLM — Claude Code summarises it later into the
gap/draft "your proposed solution" section):

* **layer1** — README, a compact folder tree, any ``PENDING``/``TODO`` file,
  plus the repo description / primary language / topics.
* **layer2** — agent modules (files named ``*agent*.py`` or living under an
  ``agents/`` directory): each one's class names + module docstring + a short
  source snippet (where the prompts usually are).
* **layer3** — benchmarks and metrics: ``benchmark*`` / ``eval*`` files, result
  JSONs, end-to-end test files.
* **layer4** — datasets (paths only — they can be huge) and configuration files
  (``*.yaml`` / ``*.toml`` / ``config*`` — small ones inlined; a real ``.env``
  is never read).

Works on a GitHub URL (``github.com/owner/repo`` — with ``@branch`` /
``/tree/branch`` / ``git@`` forms) via the REST API, or on a local checkout
path. Auth, if a private repo needs it: ``$GITHUB_TOKEN`` / ``$GH_TOKEN``, then
``~/.config/github_token``, then ``gh auth token``; with none, a private repo
just degrades to ``layers={"error": ...}`` rather than raising — the "best
effort" contract the gap-analysis skill expects.
"""

from __future__ import annotations

import base64
import fnmatch
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from strata_mcp.adapters import _http
from strata_mcp.core.entities import RepoSnapshot
from strata_mcp.core.ports import IRepoSource

GITHUB_API = "https://api.github.com"

# Directory names that are never worth introspecting.
_EXCLUDE_DIR_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "site-packages",
        ".tox",
        ".eggs",
        "vendor",
        "third_party",
        "coverage",
        ".idea",
        ".vscode",
        ".strata",
        ".apex",
    }
)

_MAX_TREE_FILES = 800
_MAX_TREE_LINES = 90
_MAX_FILE_BYTES = 256 * 1024
_README_MAX = 8_000
_PENDING_MAX = 6_000
_AGENT_SNIPPET_MAX = 6_000
_RESULT_MAX = 4_000
_CONFIG_MAX = 3_000
_MAX_AGENT_FILES = 12
_MAX_RESULT_FILES = 6
_MAX_CONFIG_FILES = 10

_README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")
_PENDING_NAMES = ("pending.md", "pending", "todo.md", "todo", "roadmap.md", "next_steps.md")
_AGENT_GLOBS = ("*agent*.py", "*agents*.py")
_BENCH_GLOBS = ("benchmark*", "*benchmark*", "*_bench*", "bench_*", "eval*.py", "*evaluation*.py")
_E2E_GLOBS = ("test_e2e*", "*e2e*test*", "e2e_*.py", "*_e2e.py")
_RESULT_GLOBS = (
    "*result*.json",
    "*results*.json",
    "*metrics*.json",
    "*benchmark*.json",
    "scores*.json",
)
_DATASET_GLOBS = ("*.csv", "*.tsv", "*.jsonl", "*.parquet", "*dataset*.json", "*.npy", "*.npz")
_CONFIG_GLOBS = (
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.cfg",
    "*.ini",
    "config*.json",
    "settings*.json",
    ".env.example",
    "*.env.example",
)
_PY_CLASS_RE = re.compile(r"^class\s+([A-Za-z_]\w*)", re.MULTILINE)
_PY_DOC_RE = re.compile(r'^\s*[rubRUB]{0,2}"""(.*?)"""', re.DOTALL)


class RepoScanError(RuntimeError):
    """A repo could not be introspected (bad reference, network/API error, or
    private without a token). Callers degrade rather than propagate."""


# --------------------------------------------------------------------------- #
# Reference parsing
# --------------------------------------------------------------------------- #
def _looks_local(ref: str) -> bool:
    s = (ref or "").strip()
    if not s:
        return False
    if s.startswith(("http://", "https://", "git@", "github.com/", "www.github.com/", "ssh://")):
        return False
    if s.startswith("file://"):
        return True
    try:
        return Path(s).expanduser().is_dir()
    except OSError:
        return False


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """``(owner/repo, branch)`` for a GitHub URL — ``branch`` is ``""`` when the
    URL doesn't pin one (the API's default branch is used then). Accepts
    ``github.com/o/r``, ``https://github.com/o/r``, ``...@branch``,
    ``.../tree/branch``, ``.../blob/branch/file`` and ``git@github.com:o/r.git``.
    Returns ``("", "")`` if it isn't a recognisable GitHub URL."""
    url = (repo_url or "").strip().rstrip("/")
    if not url:
        return "", ""
    branch = ""
    m = re.match(r"^git@github\.com:(.+)$", url)
    if m:
        url = m.group(1)
    else:
        url = re.sub(r"^(?:ssh://)?(?:https?://)?(?:www\.)?github\.com/", "", url, flags=re.I)
    if "@" in url:  # owner/repo@branch
        head, _, tail = url.rpartition("@")
        if head and "/" not in tail:
            url, branch = head.rstrip("/"), tail
    url = re.sub(r"\.git$", "", url)
    m = re.search(r"/(?:tree|blob)/([^/]+)", url)
    if m and not branch:
        branch = m.group(1)
        url = url[: m.start()]
    parts = [p for p in url.strip("/").split("/") if p]
    if len(parts) < 2:
        return "", ""
    return f"{parts[0]}/{parts[1]}", branch


# --------------------------------------------------------------------------- #
# Path classification + small text helpers (shared by the local + GitHub scans)
# --------------------------------------------------------------------------- #
def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _basename(path: str) -> str:
    return _norm(path).rsplit("/", 1)[-1]


def _is_excluded(path: str) -> bool:
    parts = _norm(path).lower().split("/")
    return any(p in _EXCLUDE_DIR_PARTS for p in parts[:-1])


def _glob_any(path: str, globs: tuple[str, ...]) -> bool:
    base = _basename(path).lower()
    return any(fnmatch.fnmatch(base, g) for g in globs)


def _in_dir_named(path: str, *names: str) -> bool:
    return any(p in names for p in _norm(path).lower().split("/")[:-1])


def _tree_summary(paths: list[str]) -> str:
    by_dir: dict[str, int] = {}
    for p in paths:
        d = _norm(p).rsplit("/", 1)[0] if "/" in _norm(p) else "."
        by_dir[d] = by_dir.get(d, 0) + 1
    lines = [f"{d}/  ({by_dir[d]} files)" for d in sorted(by_dir)]
    if len(lines) > _MAX_TREE_LINES:
        extra = len(by_dir) - _MAX_TREE_LINES
        lines = lines[:_MAX_TREE_LINES] + [f"... (+{extra} more directories)"]
    return "\n".join(lines)


def _truncate(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = text.replace("\x00", "")
    return text if len(text) <= limit else text[:limit] + "\n[... truncated ...]"


def _py_meta(source: str) -> dict:
    classes = _PY_CLASS_RE.findall(source or "")
    m = _PY_DOC_RE.match(source or "")
    doc = " ".join(m.group(1).split()).strip() if m else None
    return {"classes": classes[:20], "docstring": _truncate(doc, 600)}


def _build_layers(
    *,
    paths: list[str],
    read: Callable[[str], str | None],
    description: str | None = None,
    language: str | None = None,
    topics: list[str] | None = None,
) -> dict:
    """Assemble the four layers from a flat ``paths`` list plus a ``read(path) ->
    str | None`` callback. Pure apart from what ``read`` does."""
    paths = [p for p in paths if not _is_excluded(p)]

    # layer 1 — readme / tree / pending + repo facts
    readme = None
    for p in paths:
        if "/" not in _norm(p) and _basename(p).lower() in _README_NAMES:
            readme = _truncate(read(p), _README_MAX)
            if readme:
                break
    pending = None
    for p in paths:
        if _basename(p).lower() in _PENDING_NAMES:
            pending = _truncate(read(p), _PENDING_MAX)
            if pending:
                break
    layer1: dict = {"tree": _tree_summary(paths)}
    if readme:
        layer1["readme"] = readme
    if pending:
        layer1["pending"] = pending
    if description:
        layer1["description"] = description
    if language:
        layer1["language"] = language
    if topics:
        layer1["topics"] = topics

    # layer 2 — agents
    agent_paths = sorted(
        {
            p
            for p in paths
            if _glob_any(p, _AGENT_GLOBS)
            or (_in_dir_named(p, "agents", "agent") and p.endswith(".py"))
        }
    )
    agents = []
    for p in agent_paths[:_MAX_AGENT_FILES]:
        src = read(p) or ""
        agents.append({"path": p, **_py_meta(src), "snippet": _truncate(src, _AGENT_SNIPPET_MAX)})

    # layer 3 — benchmarks / metrics / e2e tests
    bench_files = sorted(
        {
            p
            for p in paths
            if _glob_any(p, _BENCH_GLOBS) or _in_dir_named(p, "benchmarks", "benchmark")
        }
    )
    e2e_files = sorted({p for p in paths if _glob_any(p, _E2E_GLOBS) or _in_dir_named(p, "e2e")})
    result_files = sorted(
        {
            p
            for p in paths
            if _glob_any(p, _RESULT_GLOBS) or (_in_dir_named(p, "results") and p.endswith(".json"))
        }
    )
    results = []
    for p in result_files[:_MAX_RESULT_FILES]:
        content = _truncate(read(p), _RESULT_MAX)
        if content:
            results.append({"path": p, "content": content})
    layer3: dict = {}
    if bench_files:
        layer3["benchmark_files"] = bench_files[:60]
    if e2e_files:
        layer3["e2e_test_files"] = e2e_files[:60]
    if results:
        layer3["results"] = results

    # layer 4 — datasets (paths only) / configs (small ones inlined)
    dataset_files = sorted(
        {p for p in paths if _glob_any(p, _DATASET_GLOBS) or _in_dir_named(p, "data", "datasets")}
    )
    config_files = sorted(
        {p for p in paths if _glob_any(p, _CONFIG_GLOBS) and _basename(p).lower() != ".env"}
    )
    configs = []
    for p in config_files[:_MAX_CONFIG_FILES]:
        content = _truncate(read(p), _CONFIG_MAX)
        if content:
            configs.append({"path": p, "content": content})
    layer4: dict = {}
    if dataset_files:
        layer4["dataset_files"] = dataset_files[:120]
    if configs:
        layer4["configs"] = configs

    return {
        "file_count": len(paths),
        "layer1": layer1,
        "layer2": {"agents": agents},
        "layer3": layer3,
        "layer4": layer4,
    }


# --------------------------------------------------------------------------- #
# Local checkout
# --------------------------------------------------------------------------- #
def _scan_local(root: Path) -> dict:
    if not root.is_dir():
        raise RepoScanError(f"not a directory: {root}")
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDE_DIR_PARTS]
        rel_dir = os.path.relpath(dirpath, root)
        for f in filenames:
            paths.append(_norm(f if rel_dir == "." else f"{rel_dir}/{f}"))
            if len(paths) >= _MAX_TREE_FILES:
                break
        if len(paths) >= _MAX_TREE_FILES:
            break

    def read(rel: str) -> str | None:
        fp = root / rel
        try:
            if not fp.is_file() or fp.stat().st_size > _MAX_FILE_BYTES:
                return None
            return fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    layers = _build_layers(paths=paths, read=read)
    layers["source"] = "local"
    layers["path"] = str(root)
    return layers


# --------------------------------------------------------------------------- #
# GitHub REST API
# --------------------------------------------------------------------------- #
def _github_token() -> str | None:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = (os.environ.get(var) or "").strip()
        if tok:
            return tok
    cfg = Path("~/.config/github_token").expanduser()
    try:
        if cfg.is_file():
            tok = cfg.read_text(encoding="utf-8").strip()
            if tok:
                return tok
    except OSError:
        pass
    # `gh auth token` — fixed args, no shell; failure (gh absent / not logged in) is fine.
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _gh_headers(token: str | None) -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _scan_github(owner_repo: str, branch: str) -> dict:
    token = _github_token()
    headers = _gh_headers(token)
    hint = "" if token else " (set GITHUB_TOKEN or run `gh auth login` to read a private repo)"

    def gh_json(path: str, params: dict | None = None) -> object:
        try:
            return _http.get_json(f"{GITHUB_API}/{path.lstrip('/')}", params, headers=headers)
        except _http.HttpError as exc:
            raise RepoScanError(f"GitHub API error for {path}: {exc}{hint}") from exc

    repo_meta = gh_json(f"repos/{owner_repo}")
    if not isinstance(repo_meta, dict):
        raise RepoScanError(f"unexpected GitHub response for {owner_repo}")
    branch = branch or str(repo_meta.get("default_branch") or "main")
    description = repo_meta.get("description") or None
    language = repo_meta.get("language") or None
    topics = repo_meta.get("topics") if isinstance(repo_meta.get("topics"), list) else None

    tree_resp = gh_json(f"repos/{owner_repo}/git/trees/{branch}", {"recursive": "1"})
    blobs = tree_resp.get("tree") if isinstance(tree_resp, dict) else None
    paths = [
        str(b["path"])
        for b in (blobs or [])
        if isinstance(b, dict) and b.get("type") == "blob" and b.get("path")
    ][:_MAX_TREE_FILES]

    def read(path: str) -> str | None:
        try:
            data = _http.get_json(
                f"{GITHUB_API}/repos/{owner_repo}/contents/{path}", {"ref": branch}, headers=headers
            )
        except _http.HttpError:
            return None
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            return None
        if int(data.get("size") or 0) > _MAX_FILE_BYTES:
            return None
        try:
            return base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return None

    layers = _build_layers(
        paths=paths, read=read, description=description, language=language, topics=topics
    )
    layers["source"] = "github"
    layers["repo"] = owner_repo
    layers["branch"] = branch
    return layers


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
class GithubRepoSource(IRepoSource):
    def scan(self, repo_url_or_path: str, project_id: str = "(scan)") -> RepoSnapshot:
        ref = (repo_url_or_path or "").strip()
        if not ref:
            return RepoSnapshot(
                project_id=project_id, repo_url="(none)", layers={"error": "no repo url given"}
            )
        try:
            if _looks_local(ref):
                target = ref[len("file://") :] if ref.startswith("file://") else ref
                layers = _scan_local(Path(target).expanduser())
            else:
                owner_repo, branch = parse_repo_url(ref)
                if not owner_repo:
                    raise RepoScanError(f"not a GitHub URL or an existing local path: {ref!r}")
                layers = _scan_github(owner_repo, branch)
        except RepoScanError as exc:
            layers = {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - introspection is best-effort, never fatal
            layers = {"error": f"repo scan failed: {exc}"}
        return RepoSnapshot(project_id=project_id, repo_url=ref, layers=layers)
