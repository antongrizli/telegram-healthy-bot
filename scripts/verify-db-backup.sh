#!/usr/bin/env bash
# Restore into a NEW disposable database; never overwrite an existing database.
set -euo pipefail
if [[ $# != 3 || ! "$3" =~ ^restorecheck_[a-z0-9_]+$ ]]; then
  echo 'Usage: verify-db-backup.sh CONTAINER BACKUP.dump restorecheck_UNIQUE_NAME' >&2
  exit 2
fi
container=$1
archive=$2
database=$3
[[ -r "$archive" ]] || { echo 'Backup is not readable.' >&2; exit 2; }
docker exec -i "$container" pg_restore --list < "$archive" > /dev/null
# createdb fails if the exact database already exists. No --clean / DROP commands.
docker exec "$container" sh -c 'exec createdb -U "${POSTGRES_USER:-postgres}" --template=template0 "$1"' sh "$database"
docker exec -i "$container" sh -c 'exec pg_restore -U "${POSTGRES_USER:-postgres}" --dbname="$1" --exit-on-error --no-owner --no-acl' sh "$database" < "$archive"
docker exec "$container" sh -c 'exec psql -U "${POSTGRES_USER:-postgres}" -d "$1" -v ON_ERROR_STOP=1 -c "SELECT tablename FROM pg_tables WHERE schemaname = '\''public'\'' ORDER BY tablename"' sh "$database"
echo "Restoration succeeded in $database. Verify application data before removing this test database."
