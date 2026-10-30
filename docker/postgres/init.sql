-- Runs once when the postgres container's data volume is first created.
-- app_user is what the API connects as: NOT the schema owner, so RLS always applies.
CREATE ROLE app_user LOGIN PASSWORD 'app_user_dev_only';
CREATE DATABASE dataviv_test OWNER dataviv;
