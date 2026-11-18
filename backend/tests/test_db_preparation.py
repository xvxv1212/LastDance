"""DB 설치 준비 절차(backend/db/preparation) 검증 — 새 임시 PostgreSQL.

2026-11-17 준비 점검 8개를 저장소 시험으로 옮긴 것이다(김교민, 2026-11-18 작업지시 v1.3 P1).
- 외부 URL 을 받지 않는 fresh_postgres()로 시험마다 새 로컬 인스턴스를 만든다.
  (롤은 클러스터 전역이므로 시험끼리 공유하지 않는다.)
- 조회 SQL 두 개는 읽기 전용 트랜잭션에서만 실행한다.
- 설치는 README 2절의 psql 단일 트랜잭션 순서를 그대로 쓴다.
- DB 이관 전용 CI(db-migrations)의 10개 게이트와는 별개 파일이다. 그 게이트에 합산하거나 편입하지 않는다.
범위 밖: 실제 공용·운영 DB, 연결 서비스 파일, 역할별 실제 접속·RLS 행 차단, 체크포인트 setup, 백업·복원.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psycopg
import pytest

from scripts.isolated_postgres import IsolatedPostgres, fresh_postgres

DB_ROOT = Path(__file__).resolve().parents[1] / "db"
PREP = DB_ROOT / "preparation"
PREFLIGHT = PREP / "00_preflight.sql"
INSPECT = PREP / "90_inspect_installed.sql"
INSTALL_ORDER = tuple(
    DB_ROOT / name for name in ("schema.sql", "rls_policies.sql", "knowledge_schema.sql", "roles.sql")
)
MIGRATIONS = DB_ROOT / "migrations"
UP_1 = MIGRATIONS / "v0.11_to_v0.11.1.sql"
UP_2 = MIGRATIONS / "v0.11.1_to_v0.11.2.sql"
DOWN_2 = MIGRATIONS / "v0.11.2_to_v0.11.1_rollback.sql"
DOWN_1 = MIGRATIONS / "v0.11.1_to_v0.11_rollback.sql"
SOURCE_DETAIL = "ck_extracted_facts_unknown_has_no_source_detail"
UNKNOWN_STATUS = "ck_extracted_facts_unknown_status"
ROLES = ("app_rw", "app_auth", "app_batch", "app_knowledge_loader")
BUSINESS_TABLES = (
    "users", "user_consents", "check_sessions", "documents", "extracted_facts", "inspection_reports",
    "cases", "case_events", "evidences", "audit_logs", "purge_tasks", "sources", "chunks",
)
CITIZEN = {
    "users", "user_consents", "check_sessions", "documents", "extracted_facts",
    "inspection_reports", "cases", "case_events", "evidences",
}
WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|GRANT|REVOKE|TRUNCATE|COPY|SET|DO|CALL|VACUUM|LOCK)\b",
    re.IGNORECASE,
)
SECRET_CATALOGS = re.compile(
    r"\b(pg_authid|pg_shadow|rolpassword|passwd|pg_user_mapping|pg_user_mappings)\b", re.IGNORECASE
)
Connection = psycopg.Connection[dict[str, Any]]


def _statements(path: Path) -> list[str]:
    """주석과 문자열 값을 지운 문장 목록. 문자열 안의 'CREATE'(권한 이름) 등은 명령어가 아니다."""
    text = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
    text = re.sub(r"'(?:[^']|'')*'", "''", text)
    return [s.strip() for s in text.split(";") if s.strip()]


def _read_only(conn: Connection, path: Path) -> list[dict[str, Any]]:
    """파일 전체를 읽기 전용 트랜잭션에서 실행하고 결과 묶음(열 이름·행)을 돌려준다."""
    results: list[dict[str, Any]] = []
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        cursor = conn.execute(path.read_text(encoding="utf-8"), prepare=False)
        while True:
            if cursor.description:
                results.append({"columns": [c.name for c in cursor.description], "rows": cursor.fetchall()})
            if not cursor.nextset():
                break
    return results


def _section(results: list[dict[str, Any]], column: str) -> list[dict[str, Any]]:
    found = [r["rows"] for r in results if column in r["columns"]]
    assert len(found) == 1, (column, len(found))
    rows: list[dict[str, Any]] = found[0]
    return rows


def _unknown_checks(results: list[dict[str, Any]]) -> dict[str, bool]:
    """90_inspect 의 CHECK 목록에서 두 모름(UNKNOWN) 제약이 검증된 상태로 있는지."""
    rows = {r["conname"]: r["convalidated"] for r in _section(results, "convalidated")}
    return {SOURCE_DETAIL: rows.get(SOURCE_DETAIL) is True, UNKNOWN_STATUS: rows.get(UNKNOWN_STATUS) is True}


def _expected_privileges(role: str, schema: str, table: str) -> set[str]:
    """README 3절 권한 계약."""
    if schema == "knowledge":
        if role == "app_rw":
            return {"select"}
        return {"select", "insert", "update", "delete"} if role == "app_knowledge_loader" else set()
    audit_insert = {"insert"} if table == "audit_logs" else set()
    if role == "app_rw":
        return {"select", "insert", "update"} if table in CITIZEN else audit_insert
    if role == "app_auth":
        return {"select", "insert", "update"} if table == "users" else audit_insert
    if role == "app_batch":
        if table in CITIZEN:
            return {"select", "update", "delete"}
        if table == "audit_logs":
            return {"select", "insert", "delete"}
        return {"select", "insert", "update", "delete"} if table == "purge_tasks" else set()
    return set()


def _psql(pg: IsolatedPostgres, *files: Path, single_transaction: bool) -> subprocess.CompletedProcess[str]:
    """README 의 psql 실행 방식. 대상은 이 시험이 만든 임시 인스턴스뿐이다."""
    spec = find_spec("pgserver")
    assert spec is not None and spec.origin is not None
    binary = Path(spec.origin).parent / "pginstall" / "bin" / "psql"
    arguments = [
        str(binary), "-h", "127.0.0.1", "-p", str(pg.port), "-U", pg.owner, "--no-password",
        "--dbname", "postgres", "-X", "--set=ON_ERROR_STOP=1",
    ]
    if single_transaction:
        arguments += ["--single-transaction", "--command=SET LOCAL search_path = public"]
    arguments += [f"--file={f}" for f in files]
    return subprocess.run(arguments, capture_output=True, text=True, timeout=60)  # noqa: S603


def _object_counts(conn: Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        "         WHERE n.nspname IN ('public', 'knowledge')) AS relations,"
        "       (SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)) AS roles,"
        "       (SELECT count(*) FROM pg_namespace WHERE nspname = 'knowledge') AS knowledge_schema",
        (list(ROLES),),
    ).fetchone()
    assert row is not None
    return dict(row)


@pytest.fixture()
def pg(record_testsuite_property: Callable[[str, object], None]) -> Iterator[IsolatedPostgres]:
    if find_spec("pgserver") is None:
        pytest.skip("BLOCKED: requirements-checkpoint-test.txt의 새 임시 PostgreSQL 의존성이 없습니다.")
    with fresh_postgres() as instance:
        record_testsuite_property("postgresql_server_version", instance.server_version)
        record_testsuite_property("production_db_touched", False)
        record_testsuite_property("external_database_url_used", False)
        yield instance


def _install(pg: IsolatedPostgres, *extra: Path) -> subprocess.CompletedProcess[str]:
    return _psql(pg, *INSTALL_ORDER, *extra, single_transaction=True)


def test_preparation_queries_are_select_only_and_avoid_secrets_and_rows() -> None:
    """정적 판별 범위: 문장 시작(SELECT/WITH), 변경 명령어, 비밀번호 카탈로그, 업무 표 직접 조회.

    판별하지 못하는 것: 함수 호출의 부작용, 실행 계획·성능.
    그래서 아래 DB 시험에서 읽기 전용 트랜잭션으로 다시 막는다.
    """
    business_from = re.compile(
        r"\b(FROM|JOIN)\s+(public\.|knowledge\.)?(" + "|".join(BUSINESS_TABLES) + r")\b", re.IGNORECASE
    )
    for path in (PREFLIGHT, INSPECT):
        statements = _statements(path)
        assert statements, path.name
        for statement in statements:
            assert re.match(r"(?i)(SELECT|WITH)\b", statement), (path.name, statement[:60])
            assert not WRITE_KEYWORDS.search(statement), (path.name, statement[:60])
            assert not SECRET_CATALOGS.search(statement), (path.name, statement[:60])
            assert not business_from.search(statement), (path.name, statement[:60])


def test_queries_run_read_only_on_empty_database_and_writes_are_refused(pg: IsolatedPostgres) -> None:
    with pg.connect() as conn:
        before = _object_counts(conn)
        preflight = _read_only(conn, PREFLIGHT)
        assert _section(preflight, "transaction_read_only")[0]["transaction_read_only"] == "on"
        inspect = _read_only(conn, INSPECT)
        tables = _section(inspect, "table_flags_match")
        assert len(tables) == 13 and all(r["exists"] is False for r in tables)
        assert len(_section(inspect, "role_attributes_match")) == 4
        assert _unknown_checks(inspect) == {SOURCE_DETAIL: False, UNKNOWN_STATUS: False}
        assert _object_counts(conn) == before
        # 점검 방법 자체의 확인: 같은 읽기 전용 트랜잭션에서는 변경이 거부된다.
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction), conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("CREATE TABLE public.must_not_exist (id integer)")
        assert conn.execute("SELECT to_regclass('public.must_not_exist') AS t").fetchone() == {"t": None}


def test_documented_install_matches_inspection_contract(pg: IsolatedPostgres) -> None:
    done = _install(pg)
    assert done.returncode == 0, done.stderr
    with pg.connect() as conn:
        before = _object_counts(conn)
        _read_only(conn, PREFLIGHT)
        inspect = _read_only(conn, INSPECT)
        assert _object_counts(conn) == before
    tables = _section(inspect, "table_flags_match")
    assert len(tables) == 13 and all(r["exists"] and r["table_flags_match"] for r in tables)
    assert sum(bool(r["rls_enabled"] and r["rls_forced"]) for r in tables) == 9
    roles = _section(inspect, "role_attributes_match")
    assert len(roles) == 4 and all(r["exists"] and r["role_attributes_match"] for r in roles)
    assert {r["role_name"]: r["rolbypassrls"] for r in roles} == {
        "app_rw": False, "app_auth": True, "app_batch": True, "app_knowledge_loader": False,
    }
    assert len(_section(inspect, "policyname")) == 9
    assert _unknown_checks(inspect) == {SOURCE_DETAIL: True, UNKNOWN_STATUS: True}
    grants = _section(inspect, "can_truncate")
    assert len(grants) == 52
    for row in grants:
        expected = _expected_privileges(row["role_name"], row["schema_name"], row["table_name"])
        for privilege in ("select", "insert", "update", "delete", "truncate"):
            assert row["can_" + privilege] == (privilege in expected), (row, privilege)
        assert row["is_table_owner"] is False
    checkpoint = next(r["rows"] for r in inspect if {"table_name", "schema_usage"} <= set(r["columns"]))
    assert len(checkpoint) == 12 and all(r["exists"] is False for r in checkpoint)


def test_old_migrations_are_rejected_on_latest_install(pg: IsolatedPostgres) -> None:
    assert _install(pg).returncode == 0
    with pg.connect() as conn:
        latest = _section(_read_only(conn, INSPECT), "convalidated")
        for path in (UP_1, UP_2):
            again = _psql(pg, path, single_transaction=False)
            assert again.returncode != 0, path.name
            assert "already exists" in again.stderr, again.stderr
        assert _section(_read_only(conn, INSPECT), "convalidated") == latest


def test_existing_database_version_is_identifiable_at_each_stage(pg: IsolatedPostgres) -> None:
    assert _install(pg).returncode == 0
    with pg.connect() as conn:
        latest = _section(_read_only(conn, INSPECT), "convalidated")
        conn.execute(
            f"ALTER TABLE extracted_facts DROP CONSTRAINT {SOURCE_DETAIL}, DROP CONSTRAINT {UNKNOWN_STATUS}"
        )
        stages: dict[str, list[dict[str, Any]]] = {}
        steps = (
            ("v0.11", None, (False, False)),
            ("v0.11.1", UP_1, (True, False)),
            ("v0.11.2", UP_2, (True, True)),
            ("back_v0.11.1", DOWN_2, (True, False)),
            ("back_v0.11", DOWN_1, (False, False)),
        )
        for stage, path, (has_detail, has_status) in steps:
            if path is not None:
                done = _psql(pg, path, single_transaction=False)
                assert done.returncode == 0, (stage, done.stderr)
            result = _read_only(conn, INSPECT)
            assert _unknown_checks(result) == {SOURCE_DETAIL: has_detail, UNKNOWN_STATUS: has_status}, stage
            stages[stage] = _section(result, "convalidated")
    assert stages["v0.11.2"] == latest  # 이관으로 만든 구조 = 최신 새 설치
    assert stages["back_v0.11.1"] == stages["v0.11.1"]
    assert stages["back_v0.11"] == stages["v0.11"]


def test_failed_install_rolls_back_everything(pg: IsolatedPostgres) -> None:
    with tempfile.TemporaryDirectory() as folder:
        failing = Path(folder) / "zz_synthetic_failure.sql"
        failing.write_text("SELECT 1/0;\n", encoding="utf-8")
        done = _install(pg, failing)
    assert done.returncode != 0
    assert "division by zero" in done.stderr
    with pg.connect() as conn:
        assert _object_counts(conn) == {"relations": 0, "roles": 0, "knowledge_schema": 0}
