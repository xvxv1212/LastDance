"""모름 사실의 처리 상태 제한(v0.11.1 → v0.11.2) DB 이관·되돌리기 검증 — 실제 임시 PostgreSQL.

test_db_unknown_migration.py(팀장 작성, v0.11 → v0.11.1)와 같은 방식이다.
- 외부 URL 을 받지 않는 fresh_postgres()로 매번 새 로컬 인스턴스만 만든다(운영·공용 DB 미사용).
- 변경 전 상태(v0.11.1)는 현재 schema.sql 에서 새 CHECK 하나(unknown_status)만 제거하여 합성한다.
- 위반 행은 자동 정리하지 않는다: 이관이 멈추고 행·제약이 그대로인지 확인한다.
범위 밖: 원본 백업 복원, 운영 이관, 전체 RLS 통합 검증.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.pq import TransactionStatus

from scripts.isolated_postgres import IsolatedPostgres, fresh_postgres

DB_ROOT = Path(__file__).resolve().parents[1] / "db"
CONSTRAINT = "ck_extracted_facts_unknown_status"
PREVIOUS_CONSTRAINT = "ck_extracted_facts_unknown_has_no_source_detail"  # v0.11.1 에서 추가된 제약
UPGRADE = DB_ROOT / "migrations" / "v0.11.1_to_v0.11.2.sql"
ROLLBACK = DB_ROOT / "migrations" / "v0.11.2_to_v0.11.1_rollback.sql"
SEEDED_TABLES = ("users", "check_sessions", "extracted_facts")
ALLOWED = ("EXTRACTED", "REJECTED", "ACKNOWLEDGED_UNKNOWN")
FORBIDDEN = ("CONFIRMED", "MODIFIED")
Connection = psycopg.Connection[dict[str, Any]]


@pytest.fixture(scope="module")
def migration_pg(record_testsuite_property: Callable[[str, object], None]) -> Iterator[IsolatedPostgres]:
    if find_spec("pgserver") is None:
        pytest.skip("BLOCKED: requirements-checkpoint-test.txt의 새 임시 PostgreSQL 의존성이 없습니다.")
    with fresh_postgres() as pg:
        record_testsuite_property("postgresql_server_version", pg.server_version)
        record_testsuite_property("migration_baseline", "schema.sql minus unknown_status CHECK (synthetic)")
        record_testsuite_property("production_db_touched", False)
        record_testsuite_property("external_database_url_used", False)
        yield pg


@contextmanager
def _schema(pg: IsolatedPostgres, *, drop: tuple[str, ...] = ()) -> Iterator[Connection]:
    """새 스키마에 현재 schema.sql 을 설치하고, 지정한 제약만 빼서 이전 버전을 합성한다."""
    name = "unknown_status_" + uuid.uuid4().hex
    with pg.connect() as owner:
        owner.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    with pg.connect(schema=name) as conn:
        conn.execute((DB_ROOT / "schema.sql").read_text(encoding="utf-8"))
        for constraint in drop:
            conn.execute(
                sql.SQL("ALTER TABLE extracted_facts DROP CONSTRAINT {}").format(sql.Identifier(constraint))
            )
        yield conn


def _seed(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """합성 사용자·점검 1건(실제 계정·자료 아님)."""
    user, session = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
        (user, f"status-{user.hex}@example.invalid", "synthetic-not-a-credential"),
    )
    conn.execute(
        "INSERT INTO check_sessions (id, user_id, entry_type) VALUES (%s, %s, 'REFUND_CONCERN')",
        (session, user),
    )
    return user, session


def _insert_unknown(
    conn: Connection, session: uuid.UUID, user: uuid.UUID, status: str = "EXTRACTED"
) -> uuid.UUID:
    """값 없는 모름 사실 1건. 처리된 상태면 처리자·시각도 넣는다(기존 confirmation_complete 충족)."""
    identifier = uuid.uuid4()
    processed = status != "EXTRACTED"
    conn.execute(
        "INSERT INTO extracted_facts "
        "(id, check_session_id, fact_key, source_kind, status, confirmed_by, confirmed_at) "
        "VALUES (%s, %s, 'CONTRACT_DATE', 'UNKNOWN', %s, %s, CASE WHEN %s THEN now() END)",
        (identifier, session, status, user if processed else None, processed),
    )
    return identifier


def _set_status(conn: Connection, fact: uuid.UUID, user: uuid.UUID, status: str) -> None:
    conn.execute(
        "UPDATE extracted_facts SET status = %s, confirmed_by = %s, confirmed_at = now() WHERE id = %s",
        (status, user, fact),
    )


def _constraints(conn: Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT t.relname AS table_name, c.conname AS constraint_name, "
        "c.contype AS constraint_type, c.convalidated AS validated, "
        "pg_get_constraintdef(c.oid, true) AS definition "
        "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = current_schema() ORDER BY t.relname, c.conname"
    ).fetchall()


def _rows(conn: Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        table: conn.execute(sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table))).fetchall()
        for table in SEEDED_TABLES
    }


@pytest.mark.parametrize("status", FORBIDDEN)
def test_violating_row_aborts_upgrade_without_rewriting_rows_or_constraints(
    migration_pg: IsolatedPostgres, status: str
) -> None:
    """'모름 + 확인/수정' 행이 1건 있으면 사전 검사에서 멈추고, 행·제약을 바꾸지 않는다(자동 정리 없음)."""
    with _schema(migration_pg, drop=(CONSTRAINT,)) as conn:
        user, session = _seed(conn)
        _insert_unknown(conn, session, user, status)
        _insert_unknown(conn, session, user, "ACKNOWLEDGED_UNKNOWN")  # 허용 상태는 세지 않는다
        before_rows, before_constraints = _rows(conn), _constraints(conn)

        with pytest.raises(psycopg.errors.RaiseException, match=r"변환 중단.*1건"):
            conn.execute(UPGRADE.read_text(encoding="utf-8"))
        assert conn.info.transaction_status == TransactionStatus.INERROR
        conn.rollback()  # 원본 SQL 의 BEGIN 실패 상태만 해제한다. 데이터를 정리하지 않는다.
        assert _rows(conn) == before_rows
        assert _constraints(conn) == before_constraints


def test_upgrade_requires_v0111_constraint_first(migration_pg: IsolatedPostgres) -> None:
    """v0.11.1 을 건너뛴 DB(v0.11 상태)에 실행하면 순서 오류로 멈추고 아무것도 바꾸지 않는다."""
    with _schema(migration_pg, drop=(CONSTRAINT, PREVIOUS_CONSTRAINT)) as conn:
        before = _constraints(conn)
        with pytest.raises(psycopg.errors.RaiseException, match="먼저 적용"):
            conn.execute(UPGRADE.read_text(encoding="utf-8"))
        conn.rollback()
        assert _constraints(conn) == before


def test_clean_upgrade_matches_fresh_schema_rejects_repeat_and_rolls_back_only_constraint(
    migration_pg: IsolatedPostgres,
) -> None:
    """정상 이관 = 새 설치와 같은 제약, 재실행 거절, 되돌리기 = 이전 제약. 모든 단계에서 행 변화 없음."""
    with _schema(migration_pg) as fresh, _schema(migration_pg, drop=(CONSTRAINT,)) as previous:
        expected = _constraints(fresh)
        user, session = _seed(previous)
        for status in ALLOWED:
            _insert_unknown(previous, session, user, status)
        before_rows, before_constraints = _rows(previous), _constraints(previous)

        previous.execute(UPGRADE.read_text(encoding="utf-8"))
        assert previous.info.transaction_status == TransactionStatus.IDLE
        upgraded = _constraints(previous)
        assert upgraded == expected
        assert len(upgraded) == len(before_constraints) + 1
        assert _rows(previous) == before_rows

        with pytest.raises(psycopg.errors.DuplicateObject):
            previous.execute(UPGRADE.read_text(encoding="utf-8"))
        previous.rollback()
        assert _constraints(previous) == upgraded

        previous.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert _constraints(previous) == before_constraints
        assert _rows(previous) == before_rows

        with pytest.raises(psycopg.errors.UndefinedObject):  # 이미 되돌린 DB 에서 다시 실행하면 멈춘다
            previous.execute(ROLLBACK.read_text(encoding="utf-8"))
        previous.rollback()
        assert _constraints(previous) == before_constraints


def test_direct_sql_blocks_confirmed_unknown_and_keeps_contract_paths(migration_pg: IsolatedPostgres) -> None:
    """새 설치·이관 DB 모두: 모름의 확인/수정 거부, 거부·모름 인지 허용, 값을 받은 수정(STATEMENT) 허용."""
    for migrated in (False, True):
        with _schema(migration_pg, drop=(CONSTRAINT,) if migrated else ()) as conn:
            if migrated:
                conn.execute(UPGRADE.read_text(encoding="utf-8"))
            user, session = _seed(conn)
            fact = _insert_unknown(conn, session, user)
            before = _rows(conn)
            for status in FORBIDDEN:
                with pytest.raises(psycopg.errors.CheckViolation) as blocked:
                    _set_status(conn, fact, user, status)
                assert blocked.value.diag.constraint_name == CONSTRAINT, (migrated, status)
                assert _rows(conn) == before
                with pytest.raises(psycopg.errors.CheckViolation) as inserted:
                    _insert_unknown(conn, session, user, status)
                assert inserted.value.diag.constraint_name == CONSTRAINT, (migrated, status)

            for status in ("REJECTED", "ACKNOWLEDGED_UNKNOWN"):
                _set_status(conn, fact, user, status)
                row = conn.execute(
                    "SELECT source_kind, status, value_text FROM extracted_facts WHERE id = %s", (fact,)
                ).fetchone()
                assert row == {"source_kind": "UNKNOWN", "status": status, "value_text": None}

            # 사용자가 값을 알려 준 수정(MODIFY): 근거를 STATEMENT 로 바꾸며 한 문장으로 갱신하면 허용된다.
            conn.execute(
                "UPDATE extracted_facts SET source_kind = 'STATEMENT', value_text = '2026-03-02(합성)', "
                "status = 'MODIFIED', confirmed_by = %s, confirmed_at = now() WHERE id = %s",
                (user, fact),
            )
            saved = conn.execute(
                "SELECT source_kind, status FROM extracted_facts WHERE id = %s", (fact,)
            ).fetchone()
            assert saved == {"source_kind": "STATEMENT", "status": "MODIFIED"}
