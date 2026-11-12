-- =====================================================================
-- 되돌리기 v0.9 → v0.8 (v0.8_to_v0.9.sql 의 역순, 한 트랜잭션)
-- 주의: 탈퇴 빈 행(email·password_hash NULL)은 v0.8 구조(NOT NULL)에 넣을 수 없어 있으면 중단된다.
--       그 행을 어떻게 할지(삭제 등) 먼저 결정해야 한다(자동 삭제하지 않음). 잠금 상태 값은 버려진다.
-- 벡터 지식 DB 되돌리기: DROP SCHEMA knowledge CASCADE;  권한: v0.8 roles.sql 로 재부여
-- =====================================================================
BEGIN;

DO $$
DECLARE n int;
BEGIN
    SELECT count(*) INTO n FROM users WHERE email IS NULL OR password_hash IS NULL OR status = 'WITHDRAWN';
    IF n > 0 THEN RAISE EXCEPTION '되돌리기 중단: v0.8 구조에 넣을 수 없는 계정 %건(탈퇴 빈 행)', n; END IF;
END $$;

DROP INDEX IF EXISTS ix_audit_logs_created_at;
DROP INDEX IF EXISTS ix_check_sessions_updated_at;

ALTER TABLE users
    DROP CONSTRAINT ck_users_failed_login_count_non_negative,
    DROP CONSTRAINT ck_users_withdrawn_has_no_identity,
    DROP CONSTRAINT ck_users_login_required,
    DROP COLUMN locked_until,
    DROP COLUMN failed_login_count,
    ALTER COLUMN password_hash SET NOT NULL,
    ALTER COLUMN email SET NOT NULL;

COMMENT ON TABLE users IS '사용자: 서비스 회원 계정';
COMMENT ON COLUMN users.email IS '이메일: 로그인 ID. 소문자로 정규화하여 저장';
COMMENT ON COLUMN users.password_hash IS '비밀번호 해시: Argon2id 해시(평문 저장 금지)';
COMMENT ON COLUMN users.display_name IS '표시 이름: 실명 아님. 선택 입력';
COMMENT ON COLUMN users.role IS '역할: CITIZEN/COUNSELOR/ADMIN';
COMMENT ON COLUMN users.status IS '계정 상태: ACTIVE/DORMANT/WITHDRAWN';
COMMENT ON COLUMN users.deleted_at IS '삭제일시: 논리 삭제 시각. 값이 있으면 조회에서 제외, 파기 배치 대상';

COMMIT;
