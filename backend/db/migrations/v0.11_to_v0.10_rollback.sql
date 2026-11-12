-- =====================================================================
-- 되돌리기 v0.11 → v0.10 (v0.10_to_v0.11.sql 의 역순, 한 트랜잭션)
-- 데이터를 바꾸거나 버리지 않는다. 아래 두 경우에는 '중단'한다(자동 변환·자동 삭제 금지).
--  ① '모름 인지'(ACKNOWLEDGED_UNKNOWN) 사실이 1건이라도 있으면 중단.
--     → REJECTED 등 다른 상태로 자동 변환하지 않는다. 사용자가 한 처리의 의미가 바뀌기 때문이다.
--  ② purge_tasks 에 행이 남아 있으면 중단.
--     → 아직 지우지 못한 파일·체크포인트의 유일한 기록이다. 버리면 파기 대상을 영영 잃는다.
--        먼저 정기 파기(python -m scripts.run_purge)를 돌려 0건으로 만든 뒤 다시 실행한다.
-- =====================================================================
BEGIN;

DO $$
DECLARE n_ack bigint; n_task bigint;
BEGIN
    SELECT count(*) INTO n_ack FROM extracted_facts WHERE status = 'ACKNOWLEDGED_UNKNOWN';
    IF n_ack > 0 THEN
        RAISE EXCEPTION '되돌리기 중단: 모름 인지(ACKNOWLEDGED_UNKNOWN) 사실 %건이 있습니다. 자동 변환하지 않습니다.', n_ack;
    END IF;
    SELECT count(*) INTO n_task FROM purge_tasks;
    IF n_task > 0 THEN
        RAISE EXCEPTION '되돌리기 중단: 파기 대기 작업 %건이 남아 있습니다. 정기 파기를 먼저 완료하십시오.', n_task;
    END IF;
END $$;

DROP TABLE purge_tasks;

ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_ack_only_unknown;
ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_status;
ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_status
    CHECK (status IN ('EXTRACTED', 'CONFIRMED', 'MODIFIED', 'REJECTED'));
COMMENT ON COLUMN extracted_facts.status IS '확인 상태: EXTRACTED/CONFIRMED/MODIFIED/REJECTED (CONFIRMED·MODIFIED = 승인된 사실)';

COMMIT;
