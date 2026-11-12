-- =====================================================================
-- 권리채비 AI : PostgreSQL 행 수준 보안(RLS) 정책 — DB-03 '2차 방어선'
--
-- 1차 방어선 : 애플리케이션(유스케이스의 ensure_owned_by) — 타인 자원은 404
-- 2차 방어선 : 복합 FK(owner_id 일치) + CHECK(부모 정확히 하나) — schema.sql
-- 3차 방어선 : 본 RLS — 앱 코드에 누락이 있어도 다른 사용자 행을 읽거나 쓸 수 없게 함
--
-- 사용 방법 : 앱은 트랜잭션 시작 시
--     SELECT set_config('app.user_id', '<JWT sub UUID>', true);
-- 를 실행하고, RLS 를 우회하지 않는 전용 롤(app_rw)로 접속한다.
-- 파기 배치·마이그레이션은 BYPASSRLS 롤(app_batch)로만 실행한다.
-- v0.6: 시민 자료 9개 테이블에 적용(audit_logs 는 롤 권한으로 통제).
-- v0.11: purge_tasks(파기 대기 기록)는 시민 행이 아니므로 RLS 대상이 아니다. 대신 롤 권한으로 막는다:
--        app_rw·app_auth 에는 어떤 권한도 주지 않고 app_batch 만 쓴다(db/roles.sql).
--       공식 지식(벡터 지식 DB)·지원기관(설정 파일)은 업무 DB 밖으로 옮겨 대상이 아니다.
-- =====================================================================

CREATE OR REPLACE FUNCTION app_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;

-- ---------- 소유자 컬럼을 직접 가진 테이블 ----------
DO $$
DECLARE
    t record;
BEGIN
    FOR t IN SELECT * FROM (VALUES
        ('users', 'id'),
        ('user_consents', 'user_id'),
        ('check_sessions', 'user_id'),
        ('documents', 'owner_id'),
        ('cases', 'owner_id')
    ) AS v(tbl, col)
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t.tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t.tbl);
        EXECUTE format('DROP POLICY IF EXISTS owner_only ON %I', t.tbl);
        EXECUTE format(
            'CREATE POLICY owner_only ON %I USING (%I = app_current_user()) '
            'WITH CHECK (%I = app_current_user())', t.tbl, t.col, t.col);
    END LOOP;
END $$;

-- ---------- 점검(check_sessions)에 딸린 테이블 ----------
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['extracted_facts', 'inspection_reports'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS via_session ON %I', tbl);
        EXECUTE format(
            'CREATE POLICY via_session ON %I USING (EXISTS (SELECT 1 FROM check_sessions s '
            'WHERE s.id = %I.check_session_id AND s.user_id = app_current_user()))', tbl, tbl);
    END LOOP;
END $$;

-- v0.6: 사실 확인 이력·보고서 항목·인용은 위 두 테이블의 컬럼(JSON)이 되어 별도 정책이 필요 없다.

-- ---------- 사건(cases)에 딸린 테이블 ----------
DO $$
DECLARE
    tbl text;
BEGIN
    -- 스냅샷·미확인 정보·상담 준비자료는 cases 의 컬럼이므로 cases 정책이 함께 보호한다(v0.6)
    FOREACH tbl IN ARRAY ARRAY['case_events', 'evidences'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS via_case ON %I', tbl);
        EXECUTE format(
            'CREATE POLICY via_case ON %I USING (EXISTS (SELECT 1 FROM cases c '
            'WHERE c.id = %I.case_id AND c.owner_id = app_current_user()))', tbl, tbl);
    END LOOP;
END $$;

-- v0.6: thread_id 는 check_sessions·cases 의 컬럼 → 부모 행 정책으로 보호된다(별도 테이블 없음).

-- audit_logs 는 사용자 조회 대상이 아니다: 앱 롤은 INSERT 만, 조회는 감사 전용 롤만.
