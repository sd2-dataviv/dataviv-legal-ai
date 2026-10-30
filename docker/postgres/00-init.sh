#!/bin/sh
# Runs once, when the postgres data volume is first created (docker-entrypoint-initdb.d).
# Same as init.sql, but the app_user password comes from APP_DB_PASSWORD, so a hosting platform can set a real
# one without editing files. app_user is what the API connects as: NOT the schema owner, so RLS always applies.
set -eu
psql -v ON_ERROR_STOP=1 -v pw="${APP_DB_PASSWORD:-app_user_dev_only}" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE app_user LOGIN PASSWORD :'pw';
CREATE DATABASE dataviv_test OWNER dataviv;
SQL
