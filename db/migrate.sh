#!/bin/sh
# One-shot job run on every deploy by the "migrate" service (connection from PG* variables):
# 1. applies the schema migrations below, then re-applies functions and views (02_functions_views.sql);
# 2. reloads the simulated data (03_seed.sql) when the file differs from the one loaded last time
#    (its sha256 is kept in seed_info). The data is simulated: a reload replaces all campaigns.
set -eu
INIT=/docker-entrypoint-initdb.d

# Schema migrations (idempotent), for databases created with an older 01_schema.sql:
# - platform table removed, campaign.platform holds the platform name (CASCADE drops the foreign key
#   and the views using the table; 02 recreates the views, the seed reload below rewrites the names)
psql -v ON_ERROR_STOP=1 --single-transaction \
    -c "DROP TABLE IF EXISTS platform CASCADE" \
    -f "$INIT/02_functions_views.sql"

hash=$(sha256sum "$INIT/03_seed.sql" | cut -d ' ' -f 1)
psql -v ON_ERROR_STOP=1 -qc "CREATE TABLE IF NOT EXISTS seed_info (sha256 text NOT NULL, loaded_at timestamptz NOT NULL)"
if [ "$(psql -tAc "SELECT sha256 FROM seed_info")" = "$hash" ]; then
    echo "seed data up to date ($hash)"
    exit 0
fi

echo "seed data changed: reloading $INIT/03_seed.sql"
# one transaction: the seed's own BEGIN / COMMIT lines are removed
{
    echo "BEGIN;"
    echo "TRUNCATE action, step, test, campaign RESTART IDENTITY;"
    sed -e '/^BEGIN;$/d' -e '/^COMMIT;$/d' "$INIT/03_seed.sql"
    echo "DELETE FROM seed_info;"
    echo "INSERT INTO seed_info VALUES ('$hash', now());"
    echo "COMMIT;"
} | psql -v ON_ERROR_STOP=1 -q -o /dev/null
echo "seed data loaded ($hash)"
