-- 0008 semantic search: which embedding model each passage's vector came from (Step 1c)
-- Requirements: VLT-08. Decisions: ADR-003 (Qdrant), ADR-019 (hybrid search).
-- The vectors live in Qdrant; this column lets ingestion, the reindex script and /v1/search/status
-- tell embedded passages from pending ones, and re-embed everything when the model changes.

ALTER TABLE chunks ADD COLUMN embedding_model text;
CREATE INDEX ix_chunks_unembedded ON chunks (firm_id, document_id) WHERE embedding_model IS NULL;
-- app_user already holds SELECT, INSERT, UPDATE, DELETE on chunks (0002); RLS policies cover the column.
