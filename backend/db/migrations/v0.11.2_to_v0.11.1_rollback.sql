-- =====================================================================
-- 되돌리기 v0.11.2 → v0.11.1 (한 트랜잭션, 데이터 변경 없음) — DB 담당 김교민, 2026-11-12
-- unknown_status CHECK 하나만 제거한다. ACK 상태·purge_tasks·v0.11.1 제약은 그대로 둔다.
-- 제약이 없으면 오류로 중단한다(IF EXISTS 를 쓰지 않음: 예상과 다른 상태를 조용히 넘기지 않기 위해).
-- =====================================================================
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_unknown_status;
COMMIT;
