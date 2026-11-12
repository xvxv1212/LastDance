-- =====================================================================
-- 되돌리기 v0.10 → v0.9 (v0.9_to_v0.10.sql 의 역순, 한 트랜잭션)
-- 주의: 컬럼과 CHECK 만 다시 만든다. 삭제된 confidence 값은 돌아오지 않는다(모두 NULL).
--       값을 보관해 두었다면(_archive_extracted_facts_confidence) 아래 주석의 문으로 되채운다.
--   UPDATE extracted_facts f SET confidence = a.confidence
--       FROM _archive_extracted_facts_confidence a WHERE a.id = f.id;
-- =====================================================================
BEGIN;

ALTER TABLE extracted_facts ADD COLUMN confidence FLOAT;
ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_confidence_range
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
COMMENT ON COLUMN extracted_facts.confidence IS '신뢰도: 0~1, 모델 추정치';

COMMIT;
