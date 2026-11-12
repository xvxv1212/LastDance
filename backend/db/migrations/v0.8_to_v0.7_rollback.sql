-- =====================================================================
-- 되돌리기 v0.8 → v0.7 (v0.7 코드로 되돌릴 때만). 한 트랜잭션.
-- confirm_action 은 status 로 100% 재구성된다(정보 손실 없음).
-- 주의: PDF 로만 시작한 점검(situation_text NULL)이 있으면 3번에서 멈춘다 → 먼저 처리 방법을 정한다.
-- =====================================================================
BEGIN;

-- 4. confirm_action 복원
ALTER TABLE extracted_facts ADD COLUMN confirm_action VARCHAR(30);
UPDATE extracted_facts SET confirm_action = CASE status
    WHEN 'CONFIRMED' THEN 'CONFIRM' WHEN 'MODIFIED' THEN 'MODIFY' WHEN 'REJECTED' THEN 'REJECT' END;
ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_confirm_action
    CHECK (confirm_action IN ('CONFIRM', 'MODIFY', 'REJECT'));
ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_confirmation_complete;
ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_confirmation_complete CHECK (
    (confirm_action IS NULL) = (confirmed_at IS NULL) AND (confirm_action IS NULL) = (confirmed_by IS NULL));
COMMENT ON COLUMN extracted_facts.confirm_action IS '확인 처리: CONFIRM/MODIFY/REJECT. 미확인이면 NULL (구 fact_confirmations.action)';

-- 3. situation_text 필수 복원
ALTER TABLE check_sessions ALTER COLUMN situation_text SET NOT NULL;
COMMENT ON COLUMN check_sessions.situation_text IS '상황 설명: 생활언어 입력 원문(고유식별정보 마스킹 후 저장)';
COMMENT ON COLUMN documents.retention_until IS '보관 기한: 경과 시 파기 배치가 파일·행 삭제';
COMMENT ON COLUMN cases.snapshot_report_id IS '근거 보고서 ID: 스냅샷을 만든 승인 보고서. 신규 사건 직행이면 NULL';

-- 2. G2 트리거 삭제
DROP TRIGGER IF EXISTS trg_inspection_reports_immutable ON inspection_reports;
DROP FUNCTION IF EXISTS forbid_approved_report_change();

-- 1. G1 되돌리기
ALTER TABLE cases DROP CONSTRAINT ck_cases_snapshot_report_needs_origin,
                  DROP CONSTRAINT fk_cases_snapshot_report_same_session;
ALTER TABLE cases ADD CONSTRAINT fk_cases_snapshot_report_id_inspection_reports
    FOREIGN KEY (snapshot_report_id) REFERENCES inspection_reports (id) ON DELETE SET NULL;
ALTER TABLE inspection_reports DROP CONSTRAINT uq_inspection_reports_id_check_session_id;

COMMIT;
