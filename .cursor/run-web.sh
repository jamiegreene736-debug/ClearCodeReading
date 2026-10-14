#!/usr/bin/env bash
# Foreground Django development server for the Cloud Agent "web" terminal.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
. .venv/bin/activate

set -a
# shellcheck disable=SC1091
. "$REPO_ROOT/.cursor/dev.env"
set +a

# Wait for PostgreSQL before starting the server.
for _ in $(seq 1 30); do
  if nc -z "${POSTGRES_HOST:-localhost}" "${POSTGRES_PORT:-5432}"; then break; fi
  sleep 1
done

exec python manage.py runserver 0.0.0.0:8000
