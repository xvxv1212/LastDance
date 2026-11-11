-- =====================================================================
-- 변환 v0.9 → v0.10 (한 트랜잭션)
-- 2026-11-11 팀 결정(작업지시서 v1.1): 신규 MVP AI 는 confidence 를 생성·입력·판단·표시에 쓰지 않는다.
-- extracted_facts.confidence 컬럼과 범위 CHECK 를 삭제한다(113 → 112개 컬럼).
-- 주의: 삭제한 값은 되돌리기(v0.10_to_v0.9_rollback.sql)로 복구되지 않는다(컬럼만 다시 생긴다).
--       값을 보관해야 하면 이 스크립트 실행 전에 아래 주석의 보관 문을 먼저 실행한다.
--   CREATE TABLE IF NOT EXISTS _archive_extracted_facts_confidence AS
--       SELECT id, confidence FROM extracted_facts WHERE confidence IS NOT NULL;
-- =====================================================================
BEGIN;

DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM extracted_facts WHERE confidence IS NOT NULL;
    RAISE NOTICE 'confidence 값이 있는 사실 %건(삭제 후 복구 불가)', n;
END $$;

ALTER TABLE extracted_facts
    DROP CONSTRAINT ck_extracted_facts_confidence_range,
    DROP COLUMN confidence;

COMMIT;
