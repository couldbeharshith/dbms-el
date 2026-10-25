#!/usr/bin/env bash
# Pipe the demo SQL file into the container's psql.
cd "$(dirname "$0")/.."
docker exec -i my-postgres psql -U harshith -d mydb < sql/demo.sql
