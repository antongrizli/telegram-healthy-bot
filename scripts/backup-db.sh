#!/usr/bin/env bash
# Usage: bash scripts/backup-db.sh CONTAINER /absolute/path/backup.dump
set -euo pipefail
umask 077
if [[ $# != 2 || "$2" != /* ]]; then
  echo 'Usage: backup-db.sh CONTAINER /absolute/path/backup.dump' >&2
  exit 2
fi
container=$1
archive=$2
if [[ -e "$archive" ]]; then
  echo 'Backup already exists; choose a new filename.' >&2
  exit 2
fi
temporary=$(mktemp "${archive}.partial.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
docker exec "$container" sh -c 'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" --format=custom --no-owner --no-acl' > "$temporary"
docker exec -i "$container" pg_restore --list < "$temporary" > /dev/null
# Hard-link publication fails if another process already created the target.
ln "$temporary" "$archive"
echo "Backup created: $archive"
echo 'Copy it to separate protected storage and verify restoration.'
