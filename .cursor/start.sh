#!/usr/bin/env bash
# Per-boot startup: bring up PostgreSQL and Redis and wait until they are ready.
# Idempotent: safe to run when the services are already running.
set -euo pipefail

PG_VERSION="$(ls /etc/postgresql 2>/dev/null | sort -V | tail -1)"
PG_VERSION="${PG_VERSION:-16}"

echo "==> Starting PostgreSQL cluster ${PG_VERSION}/main..."
sudo pg_ctlcluster "$PG_VERSION" main start 2>/dev/null || true

echo "==> Starting Redis..."
if ! redis-cli ping >/dev/null 2>&1; then
  sudo redis-server /etc/redis/redis.conf --daemonize yes || true
fi

echo "==> Waiting for PostgreSQL on localhost:5432..."
for _ in $(seq 1 30); do
  if nc -z localhost 5432; then break; fi
  sleep 1
done

echo "==> Waiting for Redis on localhost:6379..."
for _ in $(seq 1 30); do
  if redis-cli ping >/dev/null 2>&1; then break; fi
  sleep 1
done

echo "==> Services are up (PostgreSQL: $(nc -z localhost 5432 && echo ok || echo down), Redis: $(redis-cli ping 2>/dev/null || echo down))."
