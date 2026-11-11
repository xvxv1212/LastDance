-- =====================================================================
-- 권리채비 AI 업무 DB 마이그레이션 v0.8 → v0.9 (2026-11-10 팀 결정: MVP 미결정 5개 + 벡터 DB A안)
-- 실행 계정: 테이블 소유자(마이그레이션 계정). 한 트랜잭션으로 실행한다(실패 시 전부 취소).
-- 되돌리기: v0.9_to_v0.8_rollback.sql
--
-- 1. users: 이메일·비밀번호 로그인 유지 + 연속 실패 잠금 컬럼 추가, 탈퇴 '빈 행'(이메일·해시 NULL) 규칙
-- 2. 보관 기간 파기용 색인: check_sessions(updated_at), audit_logs(created_at)
-- 이 파일 다음에(같은 배포에서): db/knowledge_schema.sql(벡터 지식 DB) → db/roles.sql(권한 갱신)
-- 보류: extracted_facts.confidence 삭제는 AI 담당 확인 후 별도 마이그레이션(v0.9 계획 D1)
-- =====================================================================
BEGIN;

-- 0. 사전 점검 ---------------------------------------------------------------
DO $$
DECLARE n int;
BEGIN
    -- v0.8 까지는 탈퇴 기능이 없었다. 탈퇴 상태 행이 있으면 식별정보 처리 방법을 먼저 정해야 한다
    SELECT count(*) INTO n FROM users WHERE status = 'WITHDRAWN';
    IF n > 0 THEN RAISE EXCEPTION 'v0.9 사전 점검 실패: 탈퇴 상태 계정 %건(식별정보 정리 후 다시 실행)', n; END IF;
END $$;

-- 1. users -------------------------------------------------------------------
ALTER TABLE users
    ALTER COLUMN email DROP NOT NULL,
    ALTER COLUMN password_hash DROP NOT NULL,
    ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN locked_until TIMESTAMP WITH TIME ZONE;

ALTER TABLE users
    ADD CONSTRAINT ck_users_login_required CHECK (
        status = 'WITHDRAWN' OR (email IS NOT NULL AND password_hash IS NOT NULL)),
    ADD CONSTRAINT ck_users_withdrawn_has_no_identity CHECK (
        status <> 'WITHDRAWN' OR (email IS NULL AND password_hash IS NULL AND display_name IS NULL
        AND deleted_at IS NOT NULL)),
    ADD CONSTRAINT ck_users_failed_login_count_non_negative CHECK (failed_login_count >= 0);

COMMENT ON TABLE users IS '사용자: 서비스 회원 계정(탈퇴 시 식별정보를 지운 빈 행으로 남김)';
COMMENT ON COLUMN users.email IS '이메일: 로그인 ID. 소문자로 정규화하여 저장. v0.9: 탈퇴 시 NULL(같은 이메일 재가입 허용)';
COMMENT ON COLUMN users.password_hash IS '비밀번호 해시: Argon2id 해시(평문 저장 금지). v0.9: 탈퇴 시 NULL';
COMMENT ON COLUMN users.display_name IS '표시 이름: 실명 아님. 선택 입력. 탈퇴 시 NULL';
COMMENT ON COLUMN users.role IS '역할: CITIZEN/COUNSELOR/ADMIN. v0.9 MVP 는 CITIZEN 만 사용(상담사·관리자 원문 열람 없음)';
COMMENT ON COLUMN users.status IS '계정 상태: ACTIVE/DORMANT/WITHDRAWN. ACTIVE 가 아니면 모든 API 거부';
COMMENT ON COLUMN users.failed_login_count IS '로그인 실패 횟수: 연속 실패 횟수(성공 시 0). 기준 횟수에 이르면 잠금';
COMMENT ON COLUMN users.locked_until IS '잠금 해제 시각: 연속 실패로 잠긴 경우 이 시각까지 로그인 거부';
COMMENT ON COLUMN users.deleted_at IS '탈퇴일시: 탈퇴 시각. 감사 로그 보관기간이 지나면 이 빈 행도 파기';

-- 2. 보관 기간 파기용 색인 ----------------------------------------------------
CREATE INDEX ix_check_sessions_updated_at ON check_sessions (updated_at);
CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

COMMIT;
