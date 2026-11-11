-- =====================================================================
-- 권리채비 AI : DB 접속 계정(롤)과 최소 권한 — v0.8 RLS 앱 연결
--
-- app_rw    : API 서버 전용. RLS 를 우회하지 않는다(NOBYPASSRLS). 행 삭제 권한 없음(논리 삭제만).
--             감사 로그는 쓰기(INSERT)만 가능 — 수정·삭제·조회 불가(위·변조 방지, 잔여 위험 R-06).
-- app_batch : 파기 배치 전용. 모든 사용자 행을 처리해야 하므로 BYPASSRLS + DELETE.
--             v0.9: 감사 로그 보관기간(잠정 90일) 경과분 삭제를 위해 audit_logs SELECT·DELETE 추가.
-- app_auth  : v0.9 회원가입·로그인 전용. 이메일로 찾는 시점엔 '누구인지' 몰라 RLS 로 거를 수 없으므로 BYPASSRLS 이되,
--             users 조회·가입·실패 횟수 갱신과 감사 로그 쓰기만 가능하다(점검·사건·문서 등 원문 테이블 권한 없음).
-- app_knowledge_loader : v0.9 벡터 지식 DB(knowledge 스키마) 적재 전용. 업무 테이블 권한 없음.
-- 관리자(운영자) 전용 DB 계정은 두지 않는다(v0.9 팀 결정 3: 관리자는 시민 원문 열람 권한 없음).
-- 비밀번호는 이 파일에 쓰지 않는다. 운영 비밀 저장소에서 ALTER ROLE ... PASSWORD 로 설정한다.
-- 앱은 HJ_DATABASE_URL 에 app_rw, HJ_BATCH_DATABASE_URL 에 app_batch 를 쓴다.
-- 테이블은 마이그레이션 계정(소유자)이 만들고, 이 파일은 schema.sql·rls_policies.sql 다음에 실행한다.
-- =====================================================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        CREATE ROLE app_rw LOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_batch') THEN
        CREATE ROLE app_batch LOGIN NOSUPERUSER BYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_auth') THEN
        CREATE ROLE app_auth LOGIN NOSUPERUSER BYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_knowledge_loader') THEN
        CREATE ROLE app_knowledge_loader LOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO app_rw, app_batch, app_auth;

GRANT SELECT, INSERT, UPDATE ON
    users, user_consents, check_sessions, documents, extracted_facts,
    inspection_reports, cases, case_events, evidences
TO app_rw;
GRANT INSERT ON audit_logs TO app_rw;

GRANT SELECT, UPDATE, DELETE ON
    users, user_consents, check_sessions, documents, extracted_facts,
    inspection_reports, cases, case_events, evidences
TO app_batch;
GRANT INSERT ON audit_logs TO app_batch;
GRANT SELECT, DELETE ON audit_logs TO app_batch;  -- v0.9 감사 로그 보관기간 파기(조회는 탈퇴 계정 빈 행 판정용)

-- v0.9 회원가입·로그인 전용
GRANT SELECT, INSERT, UPDATE ON users TO app_auth;
GRANT INSERT ON audit_logs TO app_auth;

-- v0.9 벡터 지식 DB(knowledge_schema.sql 실행 후): 앱은 읽기만, 적재 계정만 쓰기
GRANT USAGE ON SCHEMA knowledge TO app_rw, app_knowledge_loader;
GRANT SELECT ON ALL TABLES IN SCHEMA knowledge TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA knowledge TO app_knowledge_loader;
