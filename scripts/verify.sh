#!/usr/bin/env bash
# Local equivalent of .github/workflows/ci.yml (spec §36).
# Usage: bash scripts/verify.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PY="python"
if [ -x "$ROOT/services/ai-engine/.venv/bin/python" ]; then
  PY="$ROOT/services/ai-engine/.venv/bin/python"
fi

echo "==> [backend] ruff"
cd "$ROOT/services/ai-engine"
"$PY" -m ruff check app tests scripts

echo "==> [backend] pytest"
"$PY" -m pytest -q

echo "==> [frontend] typecheck / lint / build"
cd "$ROOT"
pnpm typecheck
pnpm lint
pnpm build

echo "==> All checks passed."