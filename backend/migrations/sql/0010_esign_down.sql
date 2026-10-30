DROP FUNCTION IF EXISTS esign_open_requests(interval, integer);
DROP FUNCTION IF EXISTS esign_request_firm(text, text);
DROP TABLE IF EXISTS signature_signers CASCADE;
DROP TABLE IF EXISTS signature_requests CASCADE;
DO $$
BEGIN
    IF to_regclass('public.documents') IS NOT NULL THEN
        ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_source_check;
        UPDATE documents SET source = 'api' WHERE source = 'esign';  -- signed copies stay, as API-made files
        ALTER TABLE documents ADD CONSTRAINT documents_source_check
            CHECK (source IN ('upload', 'email', 'portal', 'api'));
    END IF;
END $$;
