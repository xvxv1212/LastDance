"""계정별 행 수준 보안(RLS) 격리 시험 — 새 임시 PostgreSQL.

2026-11-19 팀장 지시(v1.8 후속, 김교민): 앱 소유권 검사(1차 방어선)와 별개로, DB 의 RLS(3차 방어선)가
- 본인 자료는 정상 조회되고
- 다른 계정 자료와, 사용자 연결 정보(app.user_id)가 없는 상태에서는 보호 대상 행이 보이지 않는지
실제 PostgreSQL 에서 확인한다.

- 외부 URL 을 받지 않는 fresh_postgres()로 새 로컬 인스턴스만 쓴다(HJ_TEST_* 가드는 그대로).
- 설치는 db/README 의 psql 단일 트랜잭션 순서(schema → rls_policies → knowledge_schema → roles).
- 앱 접속 롤은 운영과 같은 app_rw(NOBYPASSRLS). 합성 자료는 인스턴스 소유자(초기화 계정)가 넣는다.
- 합성 자료만 쓴다. 비밀번호를 쓰지 않는다(임시 인스턴스는 127.0.0.1 trust 인증).
범위 밖: 공용·운영 DB, app_batch·app_auth 의 행 처리, 앱 HTTP 경로 전체(SQLite 앱 시험이 맡음).
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import func, select

from app.infrastructure.db import models as orm
from app.infrastructure.db.engine import build_session_factory
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from scripts.isolated_postgres import IsolatedPostgres, fresh_postgres

DB_ROOT = Path(__file__).resolve().parents[1] / "db"
INSTALL_ORDER = tuple(
    DB_ROOT / name for name in ("schema.sql", "rls_policies.sql", "knowledge_schema.sql", "roles.sql")
)
APP_ROLE = "app_rw"
# RLS 가 걸린 시민 자료 표와 '소유자'를 찾는 기준(rls_policies.sql 과 같은 목록)
CITIZEN_TABLES = (
    "users", "user_consents", "check_sessions", "documents", "extracted_facts",
    "inspection_reports", "cases", "case_events", "evidences",
)  # fmt: skip
Connection = psycopg.Connection[dict[str, Any]]


@dataclass(frozen=True)
class Seed:
    """한 계정의 합성 자료 ID(표 이름 → 행 ID)."""

    user: uuid.UUID
    rows: dict[str, uuid.UUID]


@dataclass(frozen=True)
class RlsDb:
    pg: IsolatedPostgres
    a: Seed
    b: Seed


def _psql_install(pg: IsolatedPostgres) -> subprocess.CompletedProcess[str]:
    spec = find_spec("pgserver")
    assert spec is not None and spec.origin is not None
    binary = Path(spec.origin).parent / "pginstall" / "bin" / "psql"
    arguments = [
        str(binary), "-h", "127.0.0.1", "-p", str(pg.port), "-U", pg.owner, "--no-password",
        "--dbname", "postgres", "-X", "--set=ON_ERROR_STOP=1",
        "--single-transaction", "--command=SET LOCAL search_path = public",
        *(f"--file={f}" for f in INSTALL_ORDER),
    ]  # fmt: skip
    return subprocess.run(arguments, capture_output=True, text=True, timeout=60)  # noqa: S603


def _seed(conn: Connection, label: str) -> Seed:
    """시민 자료 9개 표에 한 계정의 합성 행을 하나씩 넣는다(소유자 계정 = RLS 를 받지 않는 초기화 계정)."""
    ids = {t: uuid.uuid4() for t in CITIZEN_TABLES}
    u = ids["users"]
    s, d, c, e = ids["check_sessions"], ids["documents"], ids["cases"], ids["case_events"]
    conn.execute(
        "INSERT INTO users (id, email, password_hash, role, status)"
        " VALUES (%s, %s, 'x', 'CITIZEN', 'ACTIVE')",
        (u, f"{label}@example.invalid"),
    )
    conn.execute(
        "INSERT INTO user_consents (id, user_id, consent_type, policy_version, agreed, agreed_at)"
        " VALUES (%s, %s, 'PRIVACY_REQUIRED', '2026-11-v1', true, now())",
        (ids["user_consents"], u),
    )
    conn.execute(
        "INSERT INTO check_sessions (id, user_id, entry_type, situation_text)"
        " VALUES (%s, %s, 'REFUND_CONCERN', %s)",
        (s, u, f"합성 상황 {label}"),
    )
    conn.execute(
        "INSERT INTO documents (id, owner_id, check_session_id, original_name, mime_type, size_bytes,"
        " storage_key, sha256, retention_until)"
        " VALUES (%s, %s, %s, %s, 'application/pdf', 10, %s, %s, now() + interval '30 days')",
        (d, u, s, f"{label}.pdf", f"syn/{label}", "0" * 64),
    )
    conn.execute(
        "INSERT INTO extracted_facts"
        " (id, check_session_id, fact_key, value_text, source_kind, document_id, page_no)"
        " VALUES (%s, %s, 'PAYMENT_AMOUNT', %s, 'DOCUMENT', %s, 1)",
        (ids["extracted_facts"], s, f"{label} 600,000원", d),
    )
    conn.execute(
        "INSERT INTO inspection_reports (id, check_session_id, version, summary_text, items)"
        " VALUES (%s, %s, 1, %s, '[]'::jsonb)",
        (ids["inspection_reports"], s, f"합성 보고서 {label}"),
    )
    conn.execute(
        "INSERT INTO cases (id, owner_id, title, case_type, origin_session_id)"
        " VALUES (%s, %s, %s, 'FITNESS_REFUND', %s)",
        (c, u, f"합성 사건 {label}", s),
    )
    conn.execute(
        "INSERT INTO case_events (id, case_id, occurred_on, actor, title)"
        " VALUES (%s, %s, current_date, 'USER', %s)",
        (e, c, f"합성 사건 기록 {label}"),
    )
    conn.execute(
        "INSERT INTO evidences (id, case_id, event_id, evidence_type, title, status)"
        " VALUES (%s, %s, %s, 'CONTRACT', %s, 'HELD')",
        (ids["evidences"], c, e, f"합성 증거 {label}"),
    )
    return Seed(u, ids)


@pytest.fixture(scope="module")
def rls_db() -> Iterator[RlsDb]:
    if find_spec("pgserver") is None:
        pytest.skip("BLOCKED: requirements-checkpoint-test.txt의 새 임시 PostgreSQL 의존성이 없습니다.")
    with fresh_postgres() as pg:
        installed = _psql_install(pg)
        assert installed.returncode == 0, installed.stderr[-2000:]
        with pg.connect() as owner:
            assert owner.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone() == {
                "rolsuper": True
            }  # 합성 자료 입력용 초기화 계정(RLS 를 받지 않음). 앱 시험은 아래 app_rw 로만 한다
            with owner.transaction():
                a, b = _seed(owner, "a"), _seed(owner, "b")
        yield RlsDb(pg, a, b)


def _app(rls_db: RlsDb) -> Connection:
    """운영 앱과 같은 롤(app_rw)로 접속. autocommit=False 로 트랜잭션 단위 사용자 연결을 시험한다."""
    conn = rls_db.pg.connect(user=APP_ROLE)
    conn.autocommit = False
    return conn


def _bind(conn: Connection, user: uuid.UUID | str) -> None:
    """unit_of_work.py 와 같은 방식: 이 트랜잭션에서만 유효한 사용자 연결."""
    conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user),))


def _visible_ids(conn: Connection, table: str) -> set[uuid.UUID]:
    return {r["id"] for r in conn.execute(f"SELECT id FROM {table}").fetchall()}  # noqa: S608 (고정 목록)


def _all_ids(seed: Seed) -> set[uuid.UUID]:
    return set(seed.rows.values())


# ---------------------------------------------------------------------------
# 설치 상태: 시험 대상이 실제로 RLS 를 받는 구성인지 먼저 확인(빈 통과 방지)
# ---------------------------------------------------------------------------
def test_app_role_cannot_bypass_and_every_citizen_table_forces_rls(rls_db: RlsDb) -> None:
    with rls_db.pg.connect() as conn:
        role = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
        ).fetchone()
        assert role == {"rolsuper": False, "rolbypassrls": False}
        rows = conn.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,"
            "       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies"
            "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relname = ANY(%s)",
            (list(CITIZEN_TABLES),),
        ).fetchall()
    assert {r["relname"] for r in rows} == set(CITIZEN_TABLES)
    for r in rows:
        assert r["relrowsecurity"] and r["relforcerowsecurity"] and r["policies"] >= 1, r


# ---------------------------------------------------------------------------
# 읽기: 본인만 보이고, 남의 행·사용자 미연결은 0건
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("who", ["a", "b"])
def test_bound_user_sees_only_own_rows_in_every_citizen_table(rls_db: RlsDb, who: str) -> None:
    me, other = (rls_db.a, rls_db.b) if who == "a" else (rls_db.b, rls_db.a)
    with _app(rls_db) as conn:
        _bind(conn, me.user)
        for table in CITIZEN_TABLES:
            seen = _visible_ids(conn, table)
            assert seen == {me.rows[table]}, table  # 본인 행은 정상 조회
            assert not seen & _all_ids(other), table  # 남의 행 0건
            # 남의 ID 를 직접 지정해도 보이지 않는다(존재 여부도 드러나지 않음)
            query = f"SELECT count(*) AS n FROM {table} WHERE id = %s"  # noqa: S608 (고정 목록)
            hit = conn.execute(query, (other.rows[table],)).fetchone()
            assert hit == {"n": 0}, table
        conn.rollback()


@pytest.mark.parametrize("binding", [None, ""], ids=["unset", "empty"])
def test_no_user_binding_sees_no_protected_rows(rls_db: RlsDb, binding: str | None) -> None:
    with _app(rls_db) as conn:
        if binding is not None:
            _bind(conn, binding)
        for table in CITIZEN_TABLES:
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone() == {"n": 0}, table  # noqa: S608
        conn.rollback()


def test_binding_does_not_leak_into_next_transaction(rls_db: RlsDb) -> None:
    """set_config(..., true)는 트랜잭션 한정: 커밋·롤백 뒤 다음 트랜잭션은 다시 0건(커넥션 풀 재사용 대비)."""
    with _app(rls_db) as conn:
        _bind(conn, rls_db.a.user)
        assert conn.execute("SELECT count(*) AS n FROM check_sessions").fetchone() == {"n": 1}
        conn.commit()
        assert conn.execute("SELECT count(*) AS n FROM check_sessions").fetchone() == {"n": 0}
        conn.rollback()


# ---------------------------------------------------------------------------
# 쓰기: 남의 행 수정은 0건, 남의 이름으로 새 행은 정책 위반, 삭제·감사 조회는 권한 없음
# ---------------------------------------------------------------------------
UPDATE_COLUMN = {
    "users": "display_name", "user_consents": "policy_version", "check_sessions": "situation_text",
    "documents": "original_name", "extracted_facts": "value_text", "inspection_reports": "summary_text",
    "cases": "title", "case_events": "title", "evidences": "title",
}  # fmt: skip


def test_cross_owner_update_changes_nothing(rls_db: RlsDb) -> None:
    with _app(rls_db) as conn:
        _bind(conn, rls_db.a.user)
        for table, column in UPDATE_COLUMN.items():
            cur = conn.execute(
                f"UPDATE {table} SET {column} = %s WHERE id = %s",  # noqa: S608 (고정 목록)
                ("변조 시도", rls_db.b.rows[table]),
            )
            assert cur.rowcount == 0, table
        conn.rollback()
    with rls_db.pg.connect() as owner:  # 소유자 계정으로 B 행이 그대로인지 재확인
        assert owner.execute(
            "SELECT situation_text FROM check_sessions WHERE id = %s", (rls_db.b.rows["check_sessions"],)
        ).fetchone() == {"situation_text": "합성 상황 b"}


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO check_sessions (id, user_id, entry_type)"
        " VALUES (gen_random_uuid(), %(b_user)s, 'REFUND_CONCERN')",
        "INSERT INTO extracted_facts (id, check_session_id, fact_key, value_text, source_kind)"
        " VALUES (gen_random_uuid(), %(b_session)s, 'PAYMENT_METHOD', '현금', 'STATEMENT')",
        "INSERT INTO inspection_reports (id, check_session_id, version, summary_text, items)"
        " VALUES (gen_random_uuid(), %(b_session)s, 9, '대리 작성', '[]'::jsonb)",
        "INSERT INTO case_events (id, case_id, occurred_on, actor, title)"
        " VALUES (gen_random_uuid(), %(b_case)s, current_date, 'USER', '대리 기록')",
    ],
    ids=["session-as-other", "fact-into-other-session", "report-into-other-session", "event-into-other-case"],
)
def test_insert_on_behalf_of_other_user_violates_policy(rls_db: RlsDb, statement: str) -> None:
    params = {
        "b_user": rls_db.b.user,
        "b_session": rls_db.b.rows["check_sessions"],
        "b_case": rls_db.b.rows["cases"],
    }
    with _app(rls_db) as conn:
        _bind(conn, rls_db.a.user)
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(statement, params)
        conn.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM check_sessions WHERE id = %(a_session)s",
        "SELECT count(*) FROM audit_logs",
        "SELECT count(*) FROM purge_tasks",
    ],
    ids=["delete-own-row", "read-audit-logs", "read-purge-tasks"],
)
def test_app_role_lacks_delete_and_audit_read_privileges(rls_db: RlsDb, statement: str) -> None:
    """RLS 와 별개의 권한 경계(roles.sql): 앱 롤은 행 삭제·감사 로그 조회·파기 대기 기록 접근이 없다."""
    with _app(rls_db) as conn:
        _bind(conn, rls_db.a.user)
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
            conn.execute(statement, {"a_session": rls_db.a.rows["check_sessions"]})
        conn.rollback()


# ---------------------------------------------------------------------------
# 앱 연결부: SqlAlchemyUnitOfWork 가 실제로 사용자를 묶고, 소유자 조건 없는 조회에도 남의 행이 없다
# ---------------------------------------------------------------------------
def test_unit_of_work_binds_user_and_repositories_cannot_reach_other_rows(rls_db: RlsDb) -> None:
    url = rls_db.pg.url(user=APP_ROLE).render_as_string(hide_password=False)  # 비밀번호 없음(trust)
    engine, factory = build_session_factory(url)
    a, b = rls_db.a, rls_db.b
    try:
        with SqlAlchemyUnitOfWork(factory, a.user) as uow:
            s = uow._require()
            # 소유자 조건을 일부러 빼고 전체를 조회해도 자기 행만(앱 코드 누락 대비)
            for model, table in (
                (orm.CheckSessionModel, "check_sessions"),
                (orm.ExtractedFactModel, "extracted_facts"),
                (orm.InspectionReportModel, "inspection_reports"),
                (orm.CaseModel, "cases"),
            ):
                assert set(s.scalars(select(model.id)).all()) == {a.rows[table]}, table
            # 저장소로 남의 ID 를 직접 찾아도 '없음'
            assert uow.sessions.get(b.rows["check_sessions"]) is None
            assert uow.reports.get(b.rows["inspection_reports"]) is None
            assert uow.facts.get(b.rows["extracted_facts"]) is None
            assert uow.facts.list_by_session(b.rows["check_sessions"]) == []
            assert uow.cases.get(b.rows["cases"]) is None
            # 본인 자료는 정상 조회
            own = uow.sessions.get(a.rows["check_sessions"])
            assert own is not None and own.user_id == a.user
            uow.commit()
            # 커밋 뒤 새 트랜잭션에도 사용자가 다시 묶인다(after_begin)
            assert s.scalar(select(func.count()).select_from(orm.CheckSessionModel)) == 1
        with SqlAlchemyUnitOfWork(factory) as uow:  # 사용자 없이 접속 → 0건(기본 거부)
            s = uow._require()
            protected = (
                orm.CheckSessionModel,
                orm.ExtractedFactModel,
                orm.InspectionReportModel,
                orm.CaseModel,
            )
            for model in protected:
                assert s.scalar(select(func.count()).select_from(model)) == 0, model.__tablename__
    finally:
        engine.dispose()
