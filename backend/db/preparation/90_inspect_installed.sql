-- 권리채비 AI 설치 후 구조·권한 조회 2026-11-12
-- SELECT 전용. 업무 행·비밀번호를 조회하지 않는다. 실제 역할 접속·RLS 행 차단 시험은 별도다.
-- NULL/false/누락은 검토 대상이다. 아직 없는 객체에서도 조회 자체는 실패하지 않게 구성했다.

SELECT current_database() AS database_name, current_user AS effective_role,
       current_setting('server_version') AS server_version;

WITH expected(schema_name, table_name, expected_rls) AS (
    VALUES ('public', 'users', true), ('public', 'user_consents', true),
           ('public', 'check_sessions', true), ('public', 'documents', true),
           ('public', 'extracted_facts', true), ('public', 'inspection_reports', true),
           ('public', 'cases', true), ('public', 'case_events', true),
           ('public', 'evidences', true), ('public', 'audit_logs', false),
           ('public', 'purge_tasks', false), ('knowledge', 'sources', false),
           ('knowledge', 'chunks', false)
)
SELECT e.schema_name, e.table_name, c.oid IS NOT NULL AS exists,
       c.relkind, pg_get_userbyid(c.relowner) AS owner, e.expected_rls,
       c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced,
       (c.relkind IN ('r', 'p') AND c.relrowsecurity = e.expected_rls
        AND c.relforcerowsecurity = e.expected_rls) AS table_flags_match
FROM expected e
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = e.schema_name
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = e.table_name
ORDER BY e.schema_name, e.table_name;

WITH expected(role_name, expected_bypassrls) AS (
    VALUES ('app_rw', false), ('app_auth', true), ('app_batch', true),
           ('app_knowledge_loader', false)
)
SELECT e.role_name, r.oid IS NOT NULL AS exists, r.rolcanlogin, r.rolsuper,
       r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolbypassrls,
       e.expected_bypassrls,
       (r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreaterole
        AND NOT r.rolcreatedb AND NOT r.rolreplication
        AND r.rolbypassrls = e.expected_bypassrls) AS role_attributes_match
FROM expected e
LEFT JOIN pg_catalog.pg_roles r ON r.rolname = e.role_name
ORDER BY e.role_name;

-- 역할 속성이 맞아도 다른 역할 소속으로 얻는 과잉 권한이 있을 수 있다.
SELECT granted.rolname AS granted_role, member.rolname AS member_role,
       membership.admin_option
FROM pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE granted.rolname IN ('app_rw', 'app_auth', 'app_batch', 'app_knowledge_loader')
   OR member.rolname IN ('app_rw', 'app_auth', 'app_batch', 'app_knowledge_loader')
ORDER BY granted_role, member_role;

WITH role_names(role_name) AS (
    VALUES ('app_rw'), ('app_auth'), ('app_batch'), ('app_knowledge_loader')
), schema_names(schema_name) AS (
    VALUES ('public'), ('knowledge'), ('langgraph_checkpoints')
)
SELECT rn.role_name, sn.schema_name,
       CASE WHEN r.oid IS NOT NULL AND n.oid IS NOT NULL
            THEN has_schema_privilege(r.oid, n.oid, 'USAGE') END AS can_use_schema,
       CASE WHEN r.oid IS NOT NULL AND n.oid IS NOT NULL
            THEN has_schema_privilege(r.oid, n.oid, 'CREATE') END AS can_create_objects
FROM role_names rn CROSS JOIN schema_names sn
LEFT JOIN pg_catalog.pg_roles r ON r.rolname = rn.role_name
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = sn.schema_name
ORDER BY rn.role_name, sn.schema_name;

-- 현재 보이는 정책 전체를 원본 rls_policies.sql과 대조한다. 이름·개수만으로 동등성을 판정하지 않는다.
SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
FROM pg_catalog.pg_policies
WHERE schemaname IN ('public', 'knowledge', 'langgraph_checkpoints')
ORDER BY schemaname, tablename, policyname;

SELECT n.nspname AS schema_name, c.relname AS table_name, x.conname,
       x.convalidated, pg_get_constraintdef(x.oid) AS definition
FROM pg_catalog.pg_constraint x
JOIN pg_catalog.pg_class c ON c.oid = x.conrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'extracted_facts' AND x.contype = 'c'
ORDER BY x.conname;

SELECT e.extname, e.extversion, n.nspname AS extension_schema
FROM pg_catalog.pg_extension e
JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname = 'vector';

-- 표/역할이 없으면 권한 결과는 NULL. SELECT 결과가 true여도 RLS를 통과하는 행이 있다는 뜻은 아니다.
WITH role_names(role_name) AS (
    VALUES ('app_rw'), ('app_auth'), ('app_batch'), ('app_knowledge_loader')
), expected_tables(schema_name, table_name) AS (
    VALUES ('public', 'users'), ('public', 'user_consents'),
           ('public', 'check_sessions'), ('public', 'documents'),
           ('public', 'extracted_facts'), ('public', 'inspection_reports'),
           ('public', 'cases'), ('public', 'case_events'), ('public', 'evidences'),
           ('public', 'audit_logs'), ('public', 'purge_tasks'),
           ('knowledge', 'sources'), ('knowledge', 'chunks')
)
SELECT rn.role_name, t.schema_name, t.table_name,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'SELECT') END AS can_select,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'INSERT') END AS can_insert,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'UPDATE') END AS can_update,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'DELETE') END AS can_delete,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'TRUNCATE') END AS can_truncate,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN c.relowner = r.oid END AS is_table_owner
FROM role_names rn CROSS JOIN expected_tables t
LEFT JOIN pg_catalog.pg_roles r ON r.rolname = rn.role_name
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = t.schema_name
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = t.table_name
ORDER BY rn.role_name, t.schema_name, t.table_name;

-- 체크포인트는 선택적 별도 단계다. 미설치라면 exists=false를 미실행으로 기록한다.
WITH expected(table_name) AS (
    VALUES ('checkpoint_migrations'), ('checkpoints'), ('checkpoint_blobs'), ('checkpoint_writes')
), role_names(role_name) AS (VALUES ('app_rw'), ('app_auth'), ('app_batch'))
SELECT e.table_name, rn.role_name, c.oid IS NOT NULL AS exists,
       pg_get_userbyid(c.relowner) AS owner,
       CASE WHEN r.oid IS NOT NULL AND n.oid IS NOT NULL
            THEN has_schema_privilege(r.oid, n.oid, 'USAGE') END AS schema_usage,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'SELECT') END AS can_select,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'INSERT') END AS can_insert,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'UPDATE') END AS can_update,
       CASE WHEN r.oid IS NOT NULL AND c.oid IS NOT NULL
            THEN has_table_privilege(r.oid, c.oid, 'DELETE') END AS can_delete
FROM expected e CROSS JOIN role_names rn
LEFT JOIN pg_catalog.pg_roles r ON r.rolname = rn.role_name
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname = 'langgraph_checkpoints'
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = e.table_name
ORDER BY e.table_name, rn.role_name;
