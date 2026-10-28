#!/bin/sh
# Isolated cloud checks: never load a production database or private local data.
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
if [ -x .cloud-venv/bin/python ]; then
    runner="$repo_root/.cloud-venv/bin/python"
else
    runner=python3
fi
export DATA_DIR="$repo_root/.cloud-data"
export TMPDIR="$DATA_DIR/tmp"
export DJANGO_ENV=development
export DJANGO_DEBUG=0
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT POSTGRES_SSLMODE
unset REDIS_URL HTTPS_ONLY TRUST_PROXY
mkdir -p "$TMPDIR"
"$runner" manage.py check
"$runner" manage.py makemigrations --check --dry-run
"$runner" manage.py test tracker --verbosity 1
printf '%s\n' 'Cloud SQLite checks passed. PostgreSQL concurrency requires compose.test.yaml.'
