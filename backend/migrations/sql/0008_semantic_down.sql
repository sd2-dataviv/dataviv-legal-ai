DROP INDEX IF EXISTS ix_chunks_unembedded;
ALTER TABLE IF EXISTS chunks DROP COLUMN IF EXISTS embedding_model;
