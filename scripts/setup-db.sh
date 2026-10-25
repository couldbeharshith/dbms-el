#!/usr/bin/env bash
# Rebuilds the my-postgres container from the custom image (postgres:17 + pgvector + PostGIS).
# Keeps the same name, port, credentials and postgres_data volume, so existing data survives.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/3] Building dbms-el-postgres:17 ..."
docker build -f docker/Dockerfile.postgres -t dbms-el-postgres:17 docker/

echo "[2/3] Recreating container my-postgres ..."
docker rm -f my-postgres

echo "[3/3] Starting my-postgres ..."
docker run --name my-postgres \
  -e POSTGRES_USER=harshith \
  -e POSTGRES_PASSWORD=mysecretpassword \
  -e POSTGRES_DB=mydb \
  -p 5432:5432 \
  -v postgres_data:/var/lib/postgresql/data \
  -d dbms-el-postgres:17

echo "Waiting for postgres to accept connections ..."
until docker exec my-postgres pg_isready -U harshith -d mydb >/dev/null 2>&1; do
  sleep 1
done

echo "Verifying extensions are available:"
docker exec my-postgres psql -U harshith -d mydb -c \
  "SELECT name, default_version FROM pg_available_extensions WHERE name IN ('vector','postgis','pgcrypto') ORDER BY name;"

echo "Done. The backend creates the extensions + schema automatically on startup."
