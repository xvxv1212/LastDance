"""미확인 사실의 DB 이관·되돌리기 검증(v0.11 → v0.11.1, 실제 임시 PostgreSQL).

기존 URL을 받지 않는 fresh_postgres()로 매번 새 로컬 인스턴스만 만든다.
변경 전 상태는 현재 schema.sql에서 새 CHECK 하나를 제거하여 합성한다.
원본 v0.11 백업 복원·운영 이관·전체 PostgreSQL/RLS 통합 검증은 이 시험의 범위가 아니다.
작성자의 외부 경로·su postgres·dropdb 셸 스크립트는 실행하지 않는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from scripts.isolated_postgres import IsolatedPostgres, fresh_postgres

DB_ROOT = Path(__file__).resolve().parents[1] / "db"
CONSTRAINT = "ck_extracted_facts_unknown_has_no_source_detail"
UPGRADE = DB_ROOT / "migrations" / "v0.11_to_v0.11.1.sql"
ROLLBACK = DB_ROOT / "migrations" / "v0.11.1_to_v0.11_rollback.sql"
SEEDED_TABLES = ("users", "check_sessions", "documents", "extracted_facts")
Connection = psycopg.Connection[dict[str, Any]]


@pytest.fixture(scope="module")
def migration_pg(record_testsuite_property: Callable[[str, object], None]) -> Iterator[IsolatedPostgres]:
    if find_spec("pgserver") is None:
        pytest.skip("BLOCKED: requirements-checkpoint-test.txt의 새 임시 PostgreSQL 의존성이 없습니다.")
    # 외부 DB 환경값을 지우거나 다른 연결로 우회하지 않는다. 기존 도구가 연결 전에 차단한다.
    with fresh_postgres() as pg:
        record_testsuite_property("postgresql_server_version", pg.server_version)
        record_testsuite_property("fresh_pgdata_verified", True)
        record_testsuite_property("listen_address", "127.0.0.1")
        record_testsuite_property("unix_socket_enabled", False)
        record_testsuite_property("migration_baseline", "schema.sql minus new UNKNOWN CHECK (synthetic)")
        record_testsuite_property("production_db_touched", False)
        record_testsuite_property("external_database_url_used", False)
        yield pg


@contextmanager
def _schema(pg: IsolatedPostgres, *, previous_version: bool = False) -> Iterator[Connection]:
    name = "unknown_" + uuid.uuid4().hex
    with pg.connect() as owner:
        owner.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    with pg.connect(schema=name) as conn:
        conn.execute((DB_ROOT / "schema.sql").read_text(encoding="utf-8"))
        if previous_version:
            conn.execute(
                sql.SQL("ALTER TABLE extracted_facts DROP CONSTRAINT {}").format(sql.Identifier(CONSTRAINT))
            )
        yield conn


def _seed_parents(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """외부 파일·실제 계정 없이 같은 세션에 속한 합성 부모 행만 준비한다."""
    user, session, document = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (%s, %s, %s)",
        (user, f"migration-{user.hex}@example.invalid", "synthetic-not-a-credential"),
    )
    conn.execute(
        "INSERT INTO check_sessions (id, user_id, entry_type) VALUES (%s, %s, 'REFUND_CONCERN')",
        (session, user),
    )
    conn.execute(
        "INSERT INTO documents "
        "(id, owner_id, check_session_id, original_name, mime_type, size_bytes, "
        "storage_key, sha256, page_count, retention_until) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            document,
            user,
            session,
            "synthetic.pdf",
            "application/pdf",
            128,
            f"synthetic/{document.hex}.pdf",
            "0" * 64,
            1,
            date(2026, 12, 15),
        ),
    )
    return session, document


def _details(document: uuid.UUID) -> tuple[tuple[str, dict[str, Any]], ...]:
    # 위치 시작·끝은 기존 offset_pair 제약을 만족하도록 함께 제공한다.
    # 아래 두 경우 모두 새 UNKNOWN 제약이 차단했는지 constraint_name까지 확인한다.
    return (
        ("value_normalized", {"value_normalized": Jsonb({"amount": 0})}),
        ("ai_value_text", {"ai_value_text": "0원(합성)"}),
        ("document_id", {"document_id": document}),
        ("page_no", {"page_no": 1}),
        ("offset_start", {"offset_start": 0, "offset_end": 0}),
        ("offset_end", {"offset_start": 0, "offset_end": 1}),
        ("source_span", {"source_span": "합성 원문 구절"}),
    )


def _insert_fact(conn: Connection, session: uuid.UUID, *, known: bool = False, **details: Any) -> uuid.UUID:
    identifier = uuid.uuid4()
    values = {
        "id": identifier,
        "check_session_id": session,
        "fact_key": "PAYMENT_AMOUNT",
        "value_text": "0원(사용자 진술·합성)" if known else None,
        "source_kind": "STATEMENT" if known else "UNKNOWN",
        **details,
    }
    conn.execute(
        sql.SQL("INSERT INTO extracted_facts ({}) VALUES ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        tuple(values.values()),
    )
    return identifier


def _constraints(conn: Connection) -> list[dict[str, Any]]:
    """현재 스키마 전체의 제약 정의·검증 여부를 비교한다(내부 객체 ID 제외)."""
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


def test_invalid_unknown_aborts_upgrade_without_rewriting_rows_or_constraints(
    migration_pg: IsolatedPostgres,
) -> None:
    """금지 칸 7종을 각각 넣으면 사전 검사에서 멈추고 합성 기존 행·제약을 보존한다."""
    for index in range(7):
        with _schema(migration_pg, previous_version=True) as conn:
            session, document = _seed_parents(conn)
            field, details = _details(document)[index]
            _insert_fact(conn, session, **details)
            _insert_fact(conn, session, known=True, value_normalized=Jsonb({"amount": 0}))
            before_rows, before_constraints = _rows(conn), _constraints(conn)
            assert CONSTRAINT not in {item["constraint_name"] for item in before_constraints}

            with pytest.raises(psycopg.errors.RaiseException, match=r"변환 중단.*1건"):
                conn.execute(UPGRADE.read_text(encoding="utf-8"))
            assert conn.info.transaction_status == TransactionStatus.INERROR, field
            conn.rollback()  # 원본 SQL의 BEGIN 실패 상태만 해제한다. 데이터를 정리하지 않는다.
            assert _rows(conn) == before_rows, field
            assert _constraints(conn) == before_constraints, field


def test_clean_upgrade_matches_fresh_schema_rejects_repeat_and_rolls_back_only_constraint(
    migration_pg: IsolatedPostgres,
) -> None:
    """정상 이관·중복 적용 거절·되돌리기를 한 생애주기로 검증하고 행 변경이 없음을 확인한다."""
    with _schema(migration_pg) as fresh, _schema(migration_pg, previous_version=True) as previous:
        expected_constraints = _constraints(fresh)
        session, document = _seed_parents(previous)
        _insert_fact(previous, session)
        all_details = {key: value for _, detail in _details(document) for key, value in detail.items()}
        _insert_fact(previous, session, known=True, **all_details)
        before_rows, before_constraints = _rows(previous), _constraints(previous)

        previous.execute(UPGRADE.read_text(encoding="utf-8"))
        assert previous.info.transaction_status == TransactionStatus.IDLE
        upgraded_constraints = _constraints(previous)
        assert upgraded_constraints == expected_constraints
        assert _rows(previous) == before_rows
        assert len(upgraded_constraints) == len(before_constraints) + 1

        with pytest.raises(psycopg.errors.DuplicateObject):
            previous.execute(UPGRADE.read_text(encoding="utf-8"))
        assert previous.info.transaction_status == TransactionStatus.INERROR
        previous.rollback()
        assert _constraints(previous) == upgraded_constraints
        assert _rows(previous) == before_rows

        previous.execute(ROLLBACK.read_text(encoding="utf-8"))
        assert previous.info.transaction_status == TransactionStatus.IDLE
        assert _constraints(previous) == before_constraints
        assert _rows(previous) == before_rows


def test_direct_sql_blocks_seven_unknown_fields_and_preserves_statement_values(
    migration_pg: IsolatedPostgres,
) -> None:
    """새 설치·이관 DB 모두 UNKNOWN의 INSERT/UPDATE를 막고 STATEMENT의 실제 값은 허용한다."""
    for migrated in (False, True):
        with _schema(migration_pg, previous_version=migrated) as conn:
            if migrated:
                conn.execute(UPGRADE.read_text(encoding="utf-8"))
            session, document = _seed_parents(conn)
            unknown = _insert_fact(conn, session)
            before_rows = _rows(conn)
            for field, details in _details(document):
                with pytest.raises(psycopg.errors.CheckViolation) as inserted:
                    _insert_fact(conn, session, **details)
                assert inserted.value.diag.constraint_name == CONSTRAINT, field
                assert _rows(conn) == before_rows, field

                with pytest.raises(psycopg.errors.CheckViolation) as updated:
                    conn.execute(
                        sql.SQL("UPDATE extracted_facts SET {} WHERE id = %s").format(
                            sql.SQL(", ").join(
                                sql.SQL("{} = %s").format(sql.Identifier(key)) for key in details
                            )
                        ),
                        (*details.values(), unknown),
                    )
                assert updated.value.diag.constraint_name == CONSTRAINT, field
                assert _rows(conn) == before_rows, field

            all_details = {key: value for _, detail in _details(document) for key, value in detail.items()}
            known = _insert_fact(conn, session, known=True, **all_details)
            saved = conn.execute("SELECT * FROM extracted_facts WHERE id = %s", (known,)).fetchone()
            assert saved is not None and saved["source_kind"] == "STATEMENT"
            assert saved["value_normalized"] == {"amount": 0}
            assert all(saved[column] is not None for column in all_details)
            # 새 제약을 추가해도 기존 value_text NULL 계약은 유지된다.
            with pytest.raises(psycopg.errors.CheckViolation) as previous_contract:
                conn.execute("UPDATE extracted_facts SET value_text = '0원' WHERE id = %s", (unknown,))
            assert previous_contract.value.diag.constraint_name == "ck_extracted_facts_unknown_has_no_value"
