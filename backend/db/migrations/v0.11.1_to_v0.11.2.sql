-- =====================================================================
-- 변환 v0.11.1 → v0.11.2 (DB 초안, 미채택) — DB 담당 김교민, 2026-11-12
-- 팀장 UNKNOWN 패치의 unknown_status CHECK 를 v0.11.1 위에 최소 단위로 옮긴다.
--   미확인(UNKNOWN) 사실은 EXTRACTED / REJECTED / ACKNOWLEDGED_UNKNOWN 상태만 가진다.
--   = '값 없는 확인 사실'(UNKNOWN + CONFIRMED/MODIFIED)을 DB 가 직접 거부한다.
-- 새 상태·컬럼·API 없음. 기존 데이터 변경 없음.
-- 위반 행이 1건이라도 있으면 '중단'한다. 상태를 바꾸거나 행을 지우는 자동 정리는 하지 않는다.
-- 주의: 팀장 v0.10 초안(unknown_ack_forward_DRAFT.sql)은 v0.11 이후 DB 에 실행하지 않는다
--       (ACK 상태가 이미 있으면 그 초안 스스로 중단하며, purge_tasks 를 모른다).
-- =====================================================================
BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
DECLARE n bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'extracted_facts'::regclass
                      AND conname = 'ck_extracted_facts_unknown_has_no_source_detail') THEN
        RAISE EXCEPTION '변환 중단: v0.11.1 제약(unknown_has_no_source_detail)이 없습니다. v0.11_to_v0.11.1.sql 을 먼저 적용하세요.';
    END IF;
    SELECT count(*) INTO n FROM extracted_facts
     WHERE source_kind = 'UNKNOWN' AND status NOT IN ('EXTRACTED', 'REJECTED', 'ACKNOWLEDGED_UNKNOWN');
    IF n > 0 THEN
        RAISE EXCEPTION '변환 중단: 미확인(UNKNOWN) 사실 중 확인·수정 상태인 행 %건. 자동 정리하지 않습니다(조회: 이 파일 하단 주석).', n;
    END IF;
END $$;

ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_unknown_status
    CHECK (source_kind <> 'UNKNOWN' OR status IN ('EXTRACTED', 'REJECTED', 'ACKNOWLEDGED_UNKNOWN'));

COMMIT;

-- 위반 행 확인(읽기 전용, 값 원문은 보지 않음):
-- SELECT id, check_session_id, fact_key, status, confirmed_at
--   FROM extracted_facts
--  WHERE source_kind = 'UNKNOWN' AND status NOT IN ('EXTRACTED', 'REJECTED', 'ACKNOWLEDGED_UNKNOWN');
