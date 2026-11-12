-- =====================================================================
-- 변환 v0.10 → v0.11 (한 트랜잭션, 기존 데이터 삭제·변경 없음)
-- 2026-11-11 보완 지시 v1.4
--  ① extracted_facts.status 에 'ACKNOWLEDGED_UNKNOWN'(모름 인지) 추가 + 미확인 항목 전용 CHECK
--     (팀 승인 계약. 기존 행의 값은 바꾸지 않는다)
--  ② purge_tasks 테이블 신설: DB 밖 자료(파일·체크포인트) 삭제 대기 기록(SUP-04)
--     ※ 2026-11-12 사용자 승인: purge_tasks 공용 DB 구조 채택 승인 완료.
--        이전 제출 시점에는 미승인이었음. 승인과 실제 운영 DB 적용 완료는 구분한다.
-- 실행 주체: 스키마 소유자(마이그레이션 계정). 실행 후 db/roles.sql 을 다시 실행해 권한을 맞춘다.
-- =====================================================================
BEGIN;

-- ① 모름 인지 상태 -------------------------------------------------------
ALTER TABLE extracted_facts DROP CONSTRAINT ck_extracted_facts_status;
ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_status
    CHECK (status IN ('EXTRACTED', 'CONFIRMED', 'MODIFIED', 'REJECTED', 'ACKNOWLEDGED_UNKNOWN'));
ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_ack_only_unknown
    CHECK (status <> 'ACKNOWLEDGED_UNKNOWN' OR source_kind = 'UNKNOWN');
COMMENT ON COLUMN extracted_facts.status IS '확인 상태: EXTRACTED/CONFIRMED/MODIFIED/REJECTED/ACKNOWLEDGED_UNKNOWN (CONFIRMED·MODIFIED = 승인된 사실, ACKNOWLEDGED_UNKNOWN = 모름 인지·값 없음)';

-- ② 파기 대기 작업 -------------------------------------------------------
CREATE TABLE purge_tasks (
    id UUID NOT NULL,
    kind VARCHAR(30) NOT NULL,
    target VARCHAR(255) NOT NULL,
    attempts INTEGER DEFAULT 0 NOT NULL,
    last_error_code VARCHAR(40),
    last_attempt_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_purge_tasks PRIMARY KEY (id),
    CONSTRAINT uq_purge_tasks_kind_target UNIQUE (kind, target),
    CONSTRAINT ck_purge_tasks_attempts_non_negative CHECK (attempts >= 0),
    CONSTRAINT ck_purge_tasks_kind CHECK (kind IN ('FILE', 'CHECKPOINT'))
);
CREATE INDEX ix_purge_tasks_kind_created_at ON purge_tasks (kind, created_at);
COMMENT ON TABLE purge_tasks IS '파기 대기 작업: DB 밖 자료(파일·체크포인트) 삭제 대기 기록. DB 행을 지우는 트랜잭션에서 함께 쓰고, 삭제가 끝나면 행을 지운다(행이 남아 있다 = 아직 지우지 못했다). 시민 접속 롤은 접근 불가';
COMMENT ON COLUMN purge_tasks.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN purge_tasks.kind IS '종류: FILE(저장소 파일)/CHECKPOINT(LangGraph 체크포인트)';
COMMENT ON COLUMN purge_tasks.target IS '대상: FILE=storage_key(랜덤 키), CHECKPOINT=thread_id(랜덤). 이름·내용 등 개인정보 아님';
COMMENT ON COLUMN purge_tasks.attempts IS '시도 횟수: 삭제를 시도한 횟수';
COMMENT ON COLUMN purge_tasks.last_error_code IS '마지막 실패 코드: DELETE_FAILED/STORE_NOT_CONNECTED. 오류 원문·경로는 기록하지 않는다';
COMMENT ON COLUMN purge_tasks.last_attempt_at IS '마지막 시도일시: 아직 시도 전이면 NULL';
COMMENT ON COLUMN purge_tasks.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';

-- 권한: 파기 전용 롤만. 롤이 아직 없으면(개발 DB) 건너뛰고, roles.sql 실행 때 부여된다.
REVOKE ALL ON purge_tasks FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_batch') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON purge_tasks TO app_batch;
    END IF;
END $$;

COMMIT;
