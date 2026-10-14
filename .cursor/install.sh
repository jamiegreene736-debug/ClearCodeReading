#!/usr/bin/env bash
# Durable, idempotent setup for the Clear Code Reading Cloud Agent dev environment.
# Installs system services (PostgreSQL, Redis), Python/Node dependencies, initializes
# the local database, and applies shared tenant migrations.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export DEBIAN_FRONTEND=noninteractive

echo "==> Installing system packages (PostgreSQL, Redis, build tools)..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
  postgresql postgresql-contrib \
  redis-server \
  libpq-dev build-essential netcat-traditional \
  python3-venv

PG_VERSION="$(ls /etc/postgresql 2>/dev/null | sort -V | tail -1)"
PG_VERSION="${PG_VERSION:-16}"

echo "==> Creating Python virtual environment and installing dependencies..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "==> Installing Node dependencies and building Tailwind CSS..."
npm ci
npm run build:css

echo "==> Starting PostgreSQL to initialize the local database..."
sudo pg_ctlcluster "$PG_VERSION" main start 2>/dev/null || true
for _ in $(seq 1 30); do
  if nc -z localhost 5432; then break; fi
  sleep 1
done

echo "==> Ensuring database role and database exist..."
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='clearcode'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE ROLE clearcode LOGIN PASSWORD 'clearcode' SUPERUSER CREATEDB;"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='clearcodereading'" | grep -q 1; then
  sudo -u postgres createdb -O clearcode clearcodereading
fi

# Load local dev environment variables.
set -a
# shellcheck disable=SC1091
. "$REPO_ROOT/.cursor/dev.env"
set +a

echo "==> Applying shared tenant migrations..."
python manage.py migrate_schemas --shared --noinput

echo "==> Collecting static files..."
python manage.py collectstatic --noinput

echo "==> Install complete."
