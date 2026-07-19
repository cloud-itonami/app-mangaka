#!/usr/bin/env bash
# scripts/dev.sh — launch the local LangGraph Studio backend for lg-mangaka.
#
# What this does:
#   1. Locates a Python ≥3.11 interpreter (3.11 preferred, 3.12/3.13 OK)
#   2. Creates a .venv if missing (uv if available, stdlib venv otherwise)
#   3. Installs lg-mangaka + langgraph-cli[inmem] (dev extras)
#   4. Starts `langgraph dev` against langgraph.dev.json on 0.0.0.0:2024
#      (in-memory checkpointer — no RW_URL required for dry_run inspection)
#
# Studio UI runs in your browser and talks to http://127.0.0.1:2024 (this
# script) via the Svelte SPA at http://localhost:5173 (separate process —
# see ../appview-studio/.../svelte/).
#
# Usage:
#   bash scripts/dev.sh                 # in-memory checkpointer, port 2024
#   PORT=2025 bash scripts/dev.sh       # alternate port
#   PY=python3.12 bash scripts/dev.sh   # force a specific interpreter
#   CONFIG=langgraph.json bash scripts/dev.sh   # production-shaped config

set -euo pipefail

LG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LG_DIR"

VENV="${VENV:-$LG_DIR/.venv}"
PORT="${PORT:-2024}"
HOST="${HOST:-127.0.0.1}"
CONFIG="${CONFIG:-langgraph.dev.json}"

# ── locate Python (prefer 3.11; pyproject requires >=3.11) ────────────────
detect_py() {
  if [[ -n "${PY:-}" ]] && command -v "$PY" >/dev/null 2>&1; then
    echo "$PY"; return
  fi
  for cand in python3.11 python3.12 python3.13 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      local v
      v="$("$cand" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
      case "$v" in
        3.11|3.12|3.13) echo "$cand"; return ;;
      esac
    fi
  done
  echo ""
}

PY_BIN="$(detect_py)"
if [[ -z "$PY_BIN" ]]; then
  echo "✘ no Python 3.11/3.12/3.13 found. Install one, or set PY=path/to/python" >&2
  echo "  brew install python@3.12   # macOS recommended" >&2
  exit 1
fi
echo "→ using $($PY_BIN --version) ($PY_BIN)"

# ── create venv (uv if available, stdlib venv otherwise) ──────────────────
if [[ ! -d "$VENV" ]]; then
  if command -v uv >/dev/null 2>&1; then
    echo "→ creating venv via uv at $VENV"
    uv venv --python "$PY_BIN" "$VENV"
  else
    echo "→ creating venv via $PY_BIN -m venv at $VENV"
    "$PY_BIN" -m venv "$VENV"
  fi
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# ── install deps (uv is ~10x faster than pip) ─────────────────────────────
# kotodama is a monorepo workspace dep — install editable from path first
# (otherwise the registry lookup for "kotodama" fails). Mirrors the prod
# Dockerfile which COPYs it under /opt/kotodama and installs editable.
PYKOTODAMA_DIR="$LG_DIR/../../../40-engine/kotoba/crates/kotoba-kotodama/py"
if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet --upgrade pip
  uv pip install --quiet -e "$PYKOTODAMA_DIR"
  uv pip install --quiet -e ".[dev]"
else
  pip install --quiet --upgrade pip
  pip install --quiet -e "$PYKOTODAMA_DIR"
  pip install --quiet -e ".[dev]"
fi

if [[ -f .env ]]; then
  echo "→ loading .env"
  set -a; source .env; set +a
fi

echo "→ launching langgraph dev (config=$CONFIG, ${HOST}:${PORT})"
echo "  API:    http://${HOST}:${PORT}/docs"
echo "  UI:     http://localhost:5173/  (run \`pnpm dev\` in ../appview-studio/.../svelte/)"
echo "  Ctrl-C to stop."
echo
exec langgraph dev --config "$CONFIG" --host "$HOST" --port "$PORT" --no-browser
