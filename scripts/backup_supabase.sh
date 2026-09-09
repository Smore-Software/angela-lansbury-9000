#!/usr/bin/env bash
#
# Back up a Supabase (Postgres) database using the Supabase CLI.
#
# Produces three timestamped files under backups/<timestamp>/:
#   roles.sql   - cluster roles            (supabase db dump --role-only)
#   schema.sql  - schema / DDL             (supabase db dump)
#   data.sql    - table data (COPY format) (supabase db dump --data-only --use-copy)
#
# Restore order is roles -> schema -> data.
#
# Usage:
#   SUPABASE_DB_URL="postgresql://postgres:PASS@db.<ref>.supabase.co:5432/postgres" \
#     ./scripts/backup_supabase.sh
#
# Notes:
#   - Use the DIRECT or SESSION-pooler connection string (port 5432), NOT the
#     transaction pooler (port 6543) -- pg_dump needs a session connection.
#   - If your password has special characters they must be percent-encoded
#     in the URL (e.g. @ -> %40, # -> %23, / -> %2F).
#   - A leading "postgresql+psycopg://" (SQLAlchemy form) is normalized to
#     "postgresql://" automatically.

set -euo pipefail

DB_URL="${SUPABASE_DB_URL:-${1:-}}"
if [[ -z "${DB_URL}" ]]; then
  echo "error: set SUPABASE_DB_URL (or pass the connection string as arg 1)" >&2
  exit 1
fi

# Normalize SQLAlchemy-style driver suffix to a plain libpq URL.
DB_URL="${DB_URL/postgresql+psycopg:\/\//postgresql:\/\/}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${ROOT_DIR}/backups/${STAMP}"
mkdir -p "${OUT_DIR}"

dump() {
  local label="$1" outfile="$2"; shift 2
  echo "==> dumping ${label} -> ${outfile}"
  npx supabase db dump --db-url "${DB_URL}" -f "${outfile}" "$@"
}

dump "roles"  "${OUT_DIR}/roles.sql"  --role-only
dump "schema" "${OUT_DIR}/schema.sql"
dump "data"   "${OUT_DIR}/data.sql"   --data-only --use-copy

echo
echo "Backup complete: ${OUT_DIR}"
ls -lh "${OUT_DIR}"
