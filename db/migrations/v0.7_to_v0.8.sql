-- =====================================================================
-- 권리채비 AI 업무 DB 마이그레이션 v0.7 → v0.8 (2026-11-09 팀 승인: 결정 1~5)
-- 실행 계정: 테이블 소유자(마이그레이션 계정). 한 트랜잭션으로 실행한다(실패 시 전부 취소).
-- 되돌리기: v0.8_to_v0.7_rollback.sql
--
-- 단계                                   | 코드 배포 순서
-- 1. G1 근거 보고서 복합 FK               | 코드 변경 없음(언제든)
-- 2. G2 승인 보고서 불변 트리거           | 코드 변경 없음(언제든)
-- 3. PDF 단독 진입(situation_text NULL 허용) | v0.8 코드와 함께
-- 4. confirm_action 단계적 삭제           | 4-1 → (v0.8 코드 배포) → 4-2·4-3
--    운영 DB 가 없는 현재(MVP 개발)는 한 번에 실행해도 된다. 운영 중이면 4-1 만 먼저 실행한다.
-- =====================================================================
BEGIN;

-- 0. 사전 점검: 아래 두 쿼리 결과가 0 이 아니면 중단된다 ----------------------
DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM cases c
    LEFT JOIN inspection_reports r ON r.id = c.snapshot_report_id AND r.check_session_id = c.origin_session_id
    WHERE c.snapshot_report_id IS NOT NULL AND r.id IS NULL;
    IF n > 0 THEN RAISE EXCEPTION 'G1 사전 점검 실패: 원본 점검과 다른 근거 보고서를 가진 사건 %건', n; END IF;

    SELECT count(*) INTO n FROM extracted_facts
    WHERE NOT ((status = 'EXTRACTED' AND confirm_action IS NULL)
            OR (status = 'CONFIRMED' AND confirm_action = 'CONFIRM')
            OR (status = 'MODIFIED'  AND confirm_action = 'MODIFY')
            OR (status = 'REJECTED'  AND confirm_action = 'REJECT'));
    IF n > 0 THEN RAISE EXCEPTION 'confirm_action 사전 점검 실패: status 와 맞지 않는 행 %건', n; END IF;
END $$;

-- 1. G1 ---------------------------------------------------------------
ALTER TABLE inspection_reports
    ADD CONSTRAINT uq_inspection_reports_id_check_session_id UNIQUE (id, check_session_id);
ALTER TABLE cases DROP CONSTRAINT fk_cases_snapshot_report_id_inspection_reports;
ALTER TABLE cases
    ADD CONSTRAINT fk_cases_snapshot_report_same_session
        FOREIGN KEY (snapshot_report_id, origin_session_id)
        REFERENCES inspection_reports (id, check_session_id),
    ADD CONSTRAINT ck_cases_snapshot_report_needs_origin
        CHECK (snapshot_report_id IS NULL OR origin_session_id IS NOT NULL);

-- 2. G2 ---------------------------------------------------------------
CREATE OR REPLACE FUNCTION forbid_approved_report_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('APPROVED', 'SUPERSEDED') AND (
           NEW.summary_text IS DISTINCT FROM OLD.summary_text
        OR NEW.items::text IS DISTINCT FROM OLD.items::text
        OR NEW.version IS DISTINCT FROM OLD.version
        OR NEW.check_session_id IS DISTINCT FROM OLD.check_session_id
        OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
        OR NOT (NEW.status = OLD.status OR (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED'))
    ) THEN
        RAISE EXCEPTION 'approved report % is immutable', OLD.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_inspection_reports_immutable ON inspection_reports;
CREATE TRIGGER trg_inspection_reports_immutable
    BEFORE UPDATE ON inspection_reports
    FOR EACH ROW EXECUTE FUNCTION forbid_approved_report_change();

-- 3. PDF 단독 진입 -------------------------------------------------------
ALTER TABLE check_sessions ALTER COLUMN situation_text DROP NOT NULL;
COMMENT ON COLUMN check_sessions.situation_text IS '상황 설명: 생활언어 입력 원문(고유식별정보 마스킹 후 저장). v0.8: PDF 만으로 시작하면 NULL(이때는 계약서 PDF 가 있어야 사실 추출 가능)';
COMMENT ON COLUMN documents.retention_until IS '보관 기한: 정보용. v0.8: 문서 단독 파기 안 함, 사건·점검과 함께 파기';
COMMENT ON COLUMN cases.snapshot_report_id IS '근거 보고서 ID: 스냅샷을 만든 승인 보고서(원본 점검의 보고서만, v0.8 복합 FK). 신규 사건 직행이면 NULL';

-- 4-1. confirm_action ↔ status 일치 CHECK(삭제 전 안전장치) ------------------
ALTER TABLE extracted_facts
    ADD CONSTRAINT ck_extracted_facts_action_matches_status CHECK (
        (status = 'EXTRACTED' AND confirm_action IS NULL)
     OR (status = 'CONFIRMED' AND confirm_action = 'CONFIRM')
     OR (status = 'MODIFIED'  AND confirm_action = 'MODIFY')
     OR (status = 'REJECTED'  AND confirm_action = 'REJECT'));

-- 4-2. 확인 완결성 CHECK 를 status 기준으로 교체 ----------------------------
ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_confirmation_complete;
ALTER TABLE extracted_facts
    ADD CONSTRAINT ck_extracted_facts_confirmation_complete CHECK (
        (status = 'EXTRACTED') = (confirmed_at IS NULL)
        AND (confirmed_at IS NULL) = (confirmed_by IS NULL));

-- 4-3. 컬럼 삭제(그 컬럼에 걸린 CHECK 도 함께 삭제됨) -------------------------
ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_action_matches_status;
ALTER TABLE extracted_facts DROP COLUMN confirm_action;

COMMIT;
