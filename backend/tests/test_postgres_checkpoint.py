"""실제 PostgresSaver 3.2.0 + 매번 새 로컬 PostgreSQL의 삭제·재시도 계약.

외부 URL은 허용하지 않으며 기존 ctx/HJ_TEST_DATABASE_URL 가드를 우회하지 않는다.
이 파일의 실제 DB 시험은 scripts.isolated_postgres가 방금 만든 인스턴스만 사용한다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

from app.application.use_cases.agent import PurgeExpiredData
from app.domain.enums import PurgeTaskKind
from app.infrastructure.db.engine import build_session_factory
from app.infrastructure.db.models import PurgeTaskModel
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.services import (
    CheckpointStoreUnavailableError,
    LangGraphCheckpointStore,
    LocalFileStorage,
    SystemClock,
)
from scripts.isolated_postgres import IsolatedPostgres, IsolatedPostgresUnavailableError, fresh_postgres

DB_ROOT = Path(__file__).resolve().parents[1] / "db"


@pytest.fixture(scope="module")
def owned_pg(record_testsuite_property: Callable[[str, object], None]) -> Iterator[IsolatedPostgres]:
    if find_spec("pgserver") is None:
        pytest.skip("BLOCKED: requirements-checkpoint-test.txt의 로컬 PostgreSQL 바이너리가 없습니다.")
    with fresh_postgres() as pg:
        record_testsuite_property("postgresql_server_version", pg.server_version)
        record_testsuite_property("fresh_pgdata_verified", True)
        record_testsuite_property("listen_address", "127.0.0.1")
        record_testsuite_property("unix_socket_enabled", False)
        yield pg


def _schema(pg: IsolatedPostgres, name: str) -> Engine:
    with pg.connect() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    return create_engine(pg.url(schema=name))


def _seed(conn: psycopg.Connection[dict[str, Any]], thread: str, namespace: str = "") -> dict[str, Any]:
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    saver = PostgresSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
    checkpoint = {
        "v": 4,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "channel_values": {"case_state": {"synthetic": True, "thread": thread}},
        "channel_versions": {"case_state": "1.000000"},
        "versions_seen": {},
        "updated_channels": None,
    }
    config = saver.put(  # type: ignore[arg-type]
        {"configurable": {"thread_id": thread, "checkpoint_ns": namespace}},
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {"case_state": "1.000000"},
    )
    saver.put_writes(config, [("pending", {"synthetic": True})], "synthetic-task")
    loaded = saver.get_tuple(config)
    assert loaded is not None
    assert loaded.checkpoint["channel_values"]["case_state"] == {"synthetic": True, "thread": thread}
    return dict(config)


def _counts(conn: psycopg.Connection[dict[str, Any]], thread: str) -> dict[str, int]:
    result = {}
    for table in LangGraphCheckpointStore.TABLES:
        row = conn.execute(
            sql.SQL("SELECT count(*) AS n FROM {} WHERE thread_id = %s").format(sql.Identifier(table)),
            (thread,),
        ).fetchone()
        assert row is not None
        result[table] = row["n"]
    return result


def test_real_setup_put_delete_preserves_other_thread_and_migrations(owned_pg: IsolatedPostgres) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    schema = "cp_" + uuid.uuid4().hex
    engine = _schema(owned_pg, schema)
    try:
        store = LangGraphCheckpointStore(engine)
        store.setup()
        store.setup()  # setup 재실행 시 이미 적용한 migration은 중복하지 않는다.
        with owned_pg.connect(schema=schema) as conn:
            _seed(conn, "delete-me")
            _seed(conn, "delete-me", "synthetic-subgraph")
            preserved = _seed(conn, "keep-me")
            before_migrations = conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall()
            assert [row["v"] for row in before_migrations] == list(range(10))
            assert set(_counts(conn, "delete-me").values()) == {2}
        store.delete_thread("delete-me")
        store.delete_thread("delete-me")  # 같은 대상 재시도는 안전하다.
        with owned_pg.connect(schema=schema) as conn:
            assert set(_counts(conn, "delete-me").values()) == {0}
            assert set(_counts(conn, "keep-me").values()) == {1}
            saver = PostgresSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
            assert saver.get_tuple(preserved) is not None
            assert list(saver.list({"configurable": {"thread_id": "delete-me"}})) == []
            assert (
                conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v").fetchall() == before_migrations
            )
    finally:
        engine.dispose()


def test_partial_schema_and_migration_gap_keep_thread_for_retry(owned_pg: IsolatedPostgres) -> None:
    schema = "cp_" + uuid.uuid4().hex
    engine = _schema(owned_pg, schema)
    try:
        store = LangGraphCheckpointStore(engine)
        with pytest.raises(CheckpointStoreUnavailableError):
            store.delete_thread("delete-me")  # 삭제 중 자동 setup 금지.
        store.setup()
        with owned_pg.connect(schema=schema) as conn:
            _seed(conn, "delete-me")
            conn.execute("ALTER TABLE checkpoint_writes DROP COLUMN task_path")
        with pytest.raises(CheckpointStoreUnavailableError, match="불완전"):
            store.delete_thread("delete-me")
        with owned_pg.connect(schema=schema) as conn:
            assert set(_counts(conn, "delete-me").values()) == {1}
            conn.execute("ALTER TABLE checkpoint_writes ADD COLUMN task_path TEXT NOT NULL DEFAULT ''")
            conn.execute("DELETE FROM checkpoint_migrations WHERE v = 9")
        with pytest.raises(CheckpointStoreUnavailableError, match="migration"):
            store.delete_thread("delete-me")
        with owned_pg.connect(schema=schema) as conn:
            assert set(_counts(conn, "delete-me").values()) == {1}
        store.setup()  # 명시적인 준비 작업에서만 migration을 복구한다.
        store.delete_thread("delete-me")
        with owned_pg.connect(schema=schema) as conn:
            assert set(_counts(conn, "delete-me").values()) == {0}
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def approved_runtime(owned_pg: IsolatedPostgres) -> Iterator[IsolatedPostgres]:
    # 방금 만든 비어 있는 인스턴스에서 원본 업무 DDL/RLS/승인된 역할 SQL을 실제 실행한다.
    with owned_pg.connect() as conn:
        conn.execute((DB_ROOT / "schema.sql").read_text(encoding="utf-8"))
        conn.execute((DB_ROOT / "rls_policies.sql").read_text(encoding="utf-8"))
        conn.execute("CREATE SCHEMA knowledge")
        conn.execute((DB_ROOT / "roles.sql").read_text(encoding="utf-8"))
    engine = _schema(owned_pg, "langgraph_checkpoints")
    try:
        LangGraphCheckpointStore(engine).setup()
        with owned_pg.connect() as conn:
            conn.execute((DB_ROOT / "checkpoint_roles.sql").read_text(encoding="utf-8"))
        yield owned_pg
    finally:
        engine.dispose()


def test_real_permission_failure_persists_purge_task_then_recovers(
    approved_runtime: IsolatedPostgres,
    tmp_path: Path,
) -> None:
    pg = approved_runtime
    checkpoint_engine = create_engine(pg.url(schema="langgraph_checkpoints", user="app_batch"))
    business_engine, factory = build_session_factory(str(pg.url(user="app_batch")))
    try:
        with pg.connect(schema="langgraph_checkpoints") as conn:
            _seed(conn, "retry-target")
            _seed(conn, "unrelated-thread")
            conn.execute("REVOKE DELETE ON checkpoint_writes FROM app_batch")
        clock = SystemClock()
        uow = SqlAlchemyUnitOfWork(factory)
        with uow:
            uow.purge.enqueue_tasks(PurgeTaskKind.CHECKPOINT, ["retry-target"], clock.now())
            uow.commit()
        purge = PurgeExpiredData(
            uow,
            clock,
            LocalFileStorage(tmp_path / "files"),
            LangGraphCheckpointStore(checkpoint_engine),
        )
        failed = purge.execute()
        assert not failed.complete and failed.checkpoints == 0 and failed.pending_checkpoints == 1
        with factory() as session:
            task = session.scalars(select(PurgeTaskModel)).one()
            assert (task.target, task.attempts, task.last_error_code) == ("retry-target", 1, "DELETE_FAILED")
        with pg.connect(schema="langgraph_checkpoints") as conn:
            # 공식 delete_thread의 중간 권한 실패도 외부 트랜잭션으로 전체 롤백된다.
            assert set(_counts(conn, "retry-target").values()) == {1}
            assert set(_counts(conn, "unrelated-thread").values()) == {1}
        with pg.connect() as conn:
            conn.execute((DB_ROOT / "checkpoint_roles.sql").read_text(encoding="utf-8"))
        recovered = purge.execute()
        assert recovered.complete and recovered.checkpoints == 1 and recovered.pending_checkpoints == 0
        with factory() as session:
            assert list(session.scalars(select(PurgeTaskModel))) == []
        with pg.connect(schema="langgraph_checkpoints") as conn:
            assert set(_counts(conn, "retry-target").values()) == {0}
            assert set(_counts(conn, "unrelated-thread").values()) == {1}
        assert purge.execute().checkpoints == 0
    finally:
        business_engine.dispose()
        checkpoint_engine.dispose()


@pytest.mark.parametrize("role", ["app_rw", "app_auth"])
def test_real_citizen_and_auth_roles_cannot_access_purge_data(
    approved_runtime: IsolatedPostgres,
    role: str,
) -> None:
    with approved_runtime.connect() as owner:
        # 기존의 과한 권한이 있어도 승인된 역할 SQL 재적용으로 회수한다.
        owner.execute(sql.SQL("GRANT SELECT ON purge_tasks TO {}").format(sql.Identifier(role)))
        owner.execute((DB_ROOT / "roles.sql").read_text(encoding="utf-8"))
    with approved_runtime.connect(user=role) as conn:
        for query in (
            "SELECT count(*) FROM purge_tasks",
            "DELETE FROM purge_tasks WHERE false",
            "UPDATE purge_tasks SET attempts = 1 WHERE false",
            "INSERT INTO purge_tasks (id, kind, target) "
            "VALUES (gen_random_uuid(), 'CHECKPOINT', 'synthetic')",
            "SELECT count(*) FROM langgraph_checkpoints.checkpoints",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)


@pytest.mark.parametrize("variable", ["HJ_TEST_DATABASE_URL", "HJ_CHECKPOINT_DATABASE_URL", "PGHOST"])
def test_external_environment_is_rejected_before_local_server_creation(
    variable: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(variable, "synthetic-external-value")
    with pytest.raises(IsolatedPostgresUnavailableError, match="BLOCKED_EXTERNAL_DB_ENV"), fresh_postgres():
        pytest.fail("외부 환경값이 있는 상태에서 서버가 만들어졌습니다.")


def test_unverified_package_version_is_rejected_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.infrastructure.services as services

    engine = create_engine("postgresql+psycopg://invalid.invalid/never_connect")
    monkeypatch.setattr(services, "version", lambda _name: "unverified")

    def forbid_connect(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("버전 확인 전에 DB 연결을 시도했습니다.")

    monkeypatch.setattr(psycopg, "connect", forbid_connect)
    with pytest.raises(CheckpointStoreUnavailableError, match="버전"):
        LangGraphCheckpointStore(engine).delete_thread("synthetic")
    engine.dispose()
