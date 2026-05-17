#!/bin/sh
set -e

DB_HOST="${POSTGRES_HOST:-}"
DB_PORT="${POSTGRES_PORT:-5432}"

if [ -z "$DB_HOST" ] && [ -n "$DATABASE_URL" ]; then
  DB_HOST="$(python - <<'PY'
import os
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
parsed = urlparse(url)
print(parsed.hostname or "")
PY
)"
  DB_PORT="$(python - <<'PY'
import os
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
parsed = urlparse(url)
print(parsed.port or 5432)
PY
)"
fi

if [ -n "$DB_HOST" ]; then
  until nc -z "$DB_HOST" "$DB_PORT"; do
    echo "Waiting for PostgreSQL at $DB_HOST:$DB_PORT..."
    sleep 1
  done
fi

exec "$@"
