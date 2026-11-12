-- 되돌리기 v0.11.1 → v0.11: 제약만 제거한다(데이터 변화 없음, 손실 없음).
BEGIN;
ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_unknown_has_no_source_detail;
COMMIT;
