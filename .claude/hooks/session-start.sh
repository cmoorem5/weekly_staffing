#!/bin/bash
# Claude Code on the web: build a Python 3.12 venv with the pinned runtime +
# dev requirements so `ruff`, both test suites, and the report builders run.
# The container's default python3 is older and can't install the pinned
# Django, so plain `pip install -r requirements-dev.txt` fails there.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

PY=""
# Prefer 3.12 to match CI (.github/workflows); 3.13 as a fallback.
for candidate in python3.12 python3.13; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "session-start: no Python 3.12+ found; skipping dependency install" >&2
  exit 0
fi

VENV="$CLAUDE_PROJECT_DIR/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
fi

# Idempotent: pip skips already-satisfied pins, so a cached container is fast.
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r requirements-dev.txt

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi
