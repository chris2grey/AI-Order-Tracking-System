#!/bin/sh
# Run from any directory; keeps existing backups and never deletes business data.
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T web python manage.py backup_data --output "/backups/tracker-$stamp.zip"
docker compose exec -T web python manage.py verify_backup "/backups/tracker-$stamp.zip"
