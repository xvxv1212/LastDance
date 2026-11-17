-- 권리채비 AI 새 DB 설치 전 조회 2026-11-12
-- SELECT 전용. 비밀번호·업무 행은 조회하지 않으며 객체를 생성·변경하지 않는다.
-- 결과는 설치 가능성 검토 자료다. 이 파일만으로 설치나 권한 검증 PASS가 되지 않는다.

SELECT current_database() AS database_name,
       session_user AS login_role,
       current_user AS effective_role,
       current_setting('server_version') AS server_version,
       current_setting('server_version_num')::integer AS server_version_num,
       current_schema() AS effective_schema,
       current_setting('search_path') AS search_path,
       current_schemas(false) AS effective_search_schemas,
       current_setting('transaction_read_only') AS transaction_read_only,
       pg_is_in_recovery() AS in_recovery,
       has_database_privilege(current_user, current_database(), 'CREATE') AS can_create_schema,
       has_schema_privilege(current_user, to_regnamespace('public'), 'USAGE') AS public_usage,
       has_schema_privilege(current_user, to_regnamespace('public'), 'CREATE') AS public_create;

-- 역할 생성 자체와 BYPASSRLS 지정 권한은 별개다. 실제 서비스 추가 제한도 확인한다.
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls,
       rolcanlogin, rolinherit,
       (rolsuper OR (rolcreaterole AND rolbypassrls))
           AS role_creation_attribute_candidate
FROM pg_catalog.pg_roles
WHERE rolname = current_user;

-- 제공되는 확장과 현재 DB에 설치된 확장을 구별한다. 0행이면 제공/설치되지 않은 상태다.
SELECT name, default_version, installed_version
FROM pg_catalog.pg_available_extensions
WHERE name = 'vector';

SELECT e.extname, e.extversion, n.nspname AS extension_schema,
       pg_get_userbyid(e.extowner) AS extension_owner
FROM pg_catalog.pg_extension e
JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname = 'vector';

SELECT name, version, installed, superuser, trusted, relocatable
FROM pg_catalog.pg_available_extension_versions
WHERE name = 'vector'
ORDER BY version;

-- 역할은 클러스터 공통이다. 동명 역할이 있으면 자동 삭제·재생성·속성 변경하지 않는다.
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls,
       rolcanlogin, rolinherit, rolreplication
FROM pg_catalog.pg_roles
WHERE rolname IN ('app_rw', 'app_auth', 'app_batch', 'app_knowledge_loader')
ORDER BY rolname;

SELECT granted.rolname AS granted_role, member.rolname AS member_role,
       membership.admin_option
FROM pg_catalog.pg_auth_members membership
JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid
JOIN pg_catalog.pg_roles member ON member.oid = membership.member
WHERE granted.rolname IN ('app_rw', 'app_auth', 'app_batch', 'app_knowledge_loader')
   OR member.rolname IN ('app_rw', 'app_auth', 'app_batch', 'app_knowledge_loader')
ORDER BY granted_role, member_role;

SELECT nspname AS schema_name, pg_get_userbyid(nspowner) AS owner
FROM pg_catalog.pg_namespace
WHERE nspname IN ('public', 'knowledge', 'langgraph_checkpoints')
ORDER BY nspname;

-- 표뿐 아니라 뷰·인덱스 등 관계 객체의 충돌도 검토한다. 결과를 자동 삭제하지 않는다.
SELECT n.nspname AS schema_name, c.relname AS object_name, c.relkind,
       pg_get_userbyid(c.relowner) AS owner,
       c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('public', 'knowledge', 'langgraph_checkpoints')
ORDER BY schema_name, object_name;

SELECT n.nspname AS schema_name, p.proname AS function_name,
       pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_userbyid(p.proowner) AS owner
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN ('app_current_user', 'forbid_approved_report_change')
ORDER BY function_name, arguments;
