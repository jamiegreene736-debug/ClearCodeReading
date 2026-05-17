#!/bin/sh
set -e

if [ -n "$POSTGRES_HOST" ]; then
  until nc -z "$POSTGRES_HOST" "${POSTGRES_PORT:-5432}"; do
    echo "Waiting for PostgreSQL at $POSTGRES_HOST:${POSTGRES_PORT:-5432}..."
    sleep 1
  done
fi

exec "$@"
