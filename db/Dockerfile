# Postgres with the schema, views and simulated data loaded at first start (empty volume only).
# migrate.sh is used by the "migrate" service to update views and data on later deploys.
FROM postgres:17
COPY init/ /docker-entrypoint-initdb.d/
COPY migrate.sh /usr/local/bin/migrate.sh
