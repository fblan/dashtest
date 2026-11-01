# Postgres with the schema, views and simulated data loaded at first start (empty volume only)
FROM postgres:17
COPY init/ /docker-entrypoint-initdb.d/
