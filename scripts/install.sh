#!/usr/bin/env bash
# install.sh -- set up strata-mcp for use with Claude Code.
#
#   1. create .venv and `pip install -e ".[dev]"`
#   2. register the `strata` MCP server in ~/.claude.json (absolute venv path),
#      backing up the file first and replacing any previous `strata` entry
#   3. symlink this repo's skills/* into ~/.claude/skills/ and commands/strata.md
#      into ~/.claude/commands/  (pass --force to overwrite existing links/files)
#
# Re-running is safe (idempotent). After it finishes, restart Claude Code.
set -euo pipefail

FORCE=0
for arg in "$@"; do
  case "$arg" in
    -f|--force) FORCE=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PY="$REPO_DIR/.venv/bin/python"
SERVER_BIN="$REPO_DIR/.venv/bin/strata-mcp"
CLAUDE_JSON="$HOME/.claude.json"
SKILLS_DST="$HOME/.claude/skills"
COMMANDS_DST="$HOME/.claude/commands"

echo "strata-mcp install -- repo: $REPO_DIR"

# --- 1. venv + editable install --------------------------------------------
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "error: strata-mcp requires Python 3.10+ (found: $("$PYTHON_BIN" --version 2>&1))" >&2
  exit 1
fi

if [ ! -d "$REPO_DIR/.venv" ]; then
  echo "creating venv at $REPO_DIR/.venv"
  "$PYTHON_BIN" -m venv "$REPO_DIR/.venv"
fi
"$VENV_PY" -m pip install -q --upgrade pip
"$VENV_PY" -m pip install -q -e "$REPO_DIR[dev]"
echo "installed strata-mcp (editable) into $REPO_DIR/.venv"

# quick smoke check: the package + the MCP server module import cleanly
"$VENV_PY" - <<'PY'
import strata_mcp, strata_mcp.server  # noqa: F401
print(f"strata-mcp {strata_mcp.__version__}: imports OK ({len(__import__('asyncio').run(strata_mcp.server.mcp.list_tools()))} tools)")
PY

# --- 2. register the `strata` MCP server in ~/.claude.json -----------------
# (Alternative if you prefer the CLI:
#    claude mcp remove strata -s user 2>/dev/null || true
#    claude mcp add strata -s user -- "$SERVER_BIN")
"$VENV_PY" - "$CLAUDE_JSON" "$SERVER_BIN" <<'PY'
import json, sys, time, pathlib

cfg_path = pathlib.Path(sys.argv[1])
server_bin = sys.argv[2]

cfg = {}
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text() or "{}")
    except json.JSONDecodeError:
        sys.exit(f"error: {cfg_path} is not valid JSON; fix or move it and re-run")
    backup = cfg_path.with_suffix(f".strata-backup-{int(time.time())}")
    backup.write_text(cfg_path.read_text())
    print(f"backed up {cfg_path} -> {backup}")

if not isinstance(cfg, dict):
    sys.exit(f"error: {cfg_path} does not contain a JSON object")

servers = cfg.setdefault("mcpServers", {})
prev = servers.get("strata")
servers["strata"] = {"type": "stdio", "command": server_bin, "args": []}
cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
if prev and prev != servers["strata"]:
    print(f"replaced previous 'strata' entry ({prev.get('command', prev)!r})")
print(f"registered 'strata' -> {server_bin} in {cfg_path}")

# warn about any project-scoped 'strata' entries that would shadow the user one
for proj, pcfg in (cfg.get("projects") or {}).items():
    if isinstance(pcfg, dict) and "strata" in (pcfg.get("mcpServers") or {}):
        print(f"note: project {proj!r} also defines an 'strata' MCP server -- "
              "remove it if it points at the old apps/mcp server")
PY

# --- 3. link skills + command into ~/.claude/ ------------------------------
mkdir -p "$SKILLS_DST" "$COMMANDS_DST"

link_one() {  # link_one <src> <dst>
  local src="$1" dst="$2"
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    echo "ok: $dst (already linked)"
    return
  fi
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    if [ "$FORCE" -eq 1 ]; then
      rm -rf "$dst"
    else
      echo "skip: $dst exists (re-run with --force to replace)"
      return
    fi
  fi
  ln -s "$src" "$dst"
  echo "linked: $dst -> $src"
}

for skill_dir in "$REPO_DIR"/skills/*/; do
  [ -f "$skill_dir/SKILL.md" ] || continue
  name="$(basename "$skill_dir")"
  link_one "${skill_dir%/}" "$SKILLS_DST/$name"
done
link_one "$REPO_DIR/commands/strata.md" "$COMMANDS_DST/strata.md"

echo
echo "done. Restart Claude Code, then run /strata in a research project directory."
echo "(the library lives in ./.strata/strata.db relative to where you run Claude Code,"
echo " or set the STRATA_DB environment variable to point elsewhere.)"
