# PostgreSQL 16 with the Dataviv roles baked in: 00-init.sh creates app_user (what the API connects as, so
# row-level security applies; password from APP_DB_PASSWORD) and the dataviv_test database. Built, not mounted,
# so the image works on any host, including hosting platforms that run the compose file without this folder.
# (init.sql is the same thing with the dev password, kept for CI and infra/docker-compose.yml.)
FROM postgres:16.6-bookworm
COPY 00-init.sh /docker-entrypoint-initdb.d/00-init.sh
RUN chmod 755 /docker-entrypoint-initdb.d/00-init.sh
