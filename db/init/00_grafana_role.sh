#!/bin/bash
# Read-only role used by Grafana; its password comes from GRAFANA_RO_PASSWORD.
set -euo pipefail
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$GRAFANA_RO_PASSWORD" <<'SQL'
CREATE ROLE grafana_ro LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE kitchen TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
SQL
