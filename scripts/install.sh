#!/usr/bin/env bash
# install.sh -- set up strata-mcp for use with Claude Code.
#
#   1. create .venv and `pip install -e ".[dev]"`
#   2. register the `strata` MCP server in ~/.claude.json (absolute venv path),
#      backing up the existing file and replacing any previous `strata` entry
#   3. link this repo's skills/ into ~/.claude/skills/ and commands/strata.md
#      into ~/.claude/commands/  (use --force to overwrite without asking)
#
# SCAFFOLD: steps 2-3 are stubbed until the MCP server is implemented (phases
# 6-8). Step 1 works now.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

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
"$REPO_DIR/.venv/bin/pip" install -q --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -q -e "$REPO_DIR[dev]"
echo "installed strata-mcp (editable) into $REPO_DIR/.venv"

# --- 2. register MCP server in ~/.claude.json ------------------------------
# TODO(phases 6-8): parse ~/.claude.json, back it up to ~/.claude.json.bak,
# set mcpServers.strata = {command: "$REPO_DIR/.venv/bin/strata-mcp", args: []},
# replacing the previous `strata` entry (currently the old apps/mcp server).
echo "TODO: register 'strata' in ~/.claude.json (command: $REPO_DIR/.venv/bin/strata-mcp)"

# --- 3. link skills + command ----------------------------------------------
# TODO(phases 6-8): symlink/copy skills/* -> ~/.claude/skills/ and
# commands/strata.md -> ~/.claude/commands/strata.md (honour --force).
echo "TODO: link skills/ and commands/strata.md into ~/.claude/"

echo "done. Restart Claude Code to pick up the MCP server once step 2 is implemented."
