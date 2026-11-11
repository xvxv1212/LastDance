"""v0.11 SUP-03/04: 파기 대기 기록(purge_tasks)·재시도·체크포인트 연결 상태 구분. 합성 자료만 쓴다.

시험 대역 안내: 체크포인트 저장소는 FakeCheckpointStore(대역)다. 여기서 확인하는 것은
'대상을 잃지 않고 다시 시도한다'는 백엔드 동작이며, 실제 LangGraph 체크포인트 파기의 종단간 확인이 아니다.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text

from app.application.use_cases import agent as uc_agent
from app.config import Settings
from app.infrastructure.db import models as orm
from app.infrastructure.services import (
    CheckpointStoreUnavailableError,
    LangGraphCheckpointStore,
    NoCheckpointStore,
)
from app.interface.cli import purge as cli_purge
from tests.conftest import PASSWORD, Ctx
from tests.test_change_requests import _approved, _count, _thread

API = "/api/v1"


def _prepare(ctx: Ctx, user: uuid.UUID) -> tuple[str, str, Path]:
    """승인까지 끝난 점검 1건 + 계약서 파일 1개 + AI 스레드 1개. (점검 ID, thread_id, 파일 경로)"""
    before = set(ctx.files_dir.iterdir()) if ctx.files_dir.exists() else set()
    sid, _, _ = _approved(ctx, user)
    thread_id = _thread(ctx, user, sid)
    (new_file,) = set(ctx.files_dir.iterdir()) - before
    return sid, thread_id, new_file


def _withdraw(ctx: Ctx, user: uuid.UUID) -> None:
    r = ctx.client.post(f"{API}/me/withdrawal", headers=ctx.auth(user), json={"password": PASSWORD})
    assert r.status_code == 204, r.text


def _tasks(ctx: Ctx) -> list[tuple[str, str, int, str | None]]:
    with ctx.factory() as s:
        rows = s.scalars(select(orm.PurgeTaskModel).order_by(orm.PurgeTaskModel.kind)).all()
        return [(str(r.kind), r.target, r.attempts, r.last_error_code) for r in rows]


def _user_rows(ctx: Ctx, user: uuid.UUID) -> tuple[int, int]:
    return _count(ctx, orm.CheckSessionModel, user_id=user), _count(ctx, orm.DocumentModel, owner_id=user)


def test_file_failure_keeps_target_and_next_batch_deletes_it(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4 재현 4번의 반대: DB 커밋 뒤 파일 삭제가 실패해도 다음 배치가 그 파일을 지운다."""
    _, thread_id, file_a = _prepare(ctx, ctx.user_a)
    real_delete = ctx.container.storage.delete

    def fail_delete(key: str) -> None:
        raise OSError("synthetic temporary file storage failure")

    monkeypatch.setattr(ctx.container.storage, "delete", fail_delete)
    _withdraw(ctx, ctx.user_a)
    assert _user_rows(ctx, ctx.user_a) == (0, 0)  # DB 행은 지워짐
    assert file_a.exists()  # 파일은 아직 남음
    assert thread_id in ctx.checkpoints.deleted  # 체크포인트는 파일 실패와 무관하게 처리됨
    tasks = _tasks(ctx)
    assert len(tasks) == 1 and tasks[0][0] == "FILE" and tasks[0][2:] == (1, "DELETE_FAILED")

    # 저장소가 계속 고장이면 배치는 '미완료'로 보고하고 대상을 유지한다
    report = ctx.container.purge_expired_data().execute()
    assert (report.files, report.pending_files, report.complete) == (0, 1, False)
    assert _tasks(ctx)[0][2] == 2 and file_a.exists()

    monkeypatch.setattr(ctx.container.storage, "delete", real_delete)  # 저장소 복구
    report = ctx.container.purge_expired_data().execute()
    assert (report.files, report.pending_files, report.complete) == (1, 0, True)
    assert not file_a.exists() and _tasks(ctx) == []


def test_checkpoint_failure_keeps_target_and_is_retried_separately(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, thread_id, file_a = _prepare(ctx, ctx.user_a)

    def fail_thread(tid: str) -> None:
        raise ConnectionError("synthetic checkpoint store failure")

    monkeypatch.setattr(ctx.checkpoints, "delete_thread", fail_thread)
    _withdraw(ctx, ctx.user_a)
    assert _user_rows(ctx, ctx.user_a) == (0, 0)
    assert not file_a.exists()  # 파일은 체크포인트 실패와 무관하게 지워짐
    assert _tasks(ctx) == [("CHECKPOINT", thread_id, 1, "DELETE_FAILED")]
    monkeypatch.undo()
    report = ctx.container.purge_expired_data().execute()
    assert (report.checkpoints, report.pending_checkpoints, report.complete) == (1, 0, True)
    assert ctx.checkpoints.deleted == [thread_id] and _tasks(ctx) == []


def test_crash_after_db_commit_then_restart_finishes_purge(ctx: Ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 커밋 직후 프로세스가 죽은 상황: 대상은 DB 에 남아 있고, 재시작한 배치가 마저 지운다."""
    _, thread_id, file_a = _prepare(ctx, ctx.user_a)

    def crash(*_: object, **__: object) -> object:
        raise SystemExit("synthetic process crash right after DB commit")

    # 탈퇴 직후 즉시 파기는 건너뛰게 하고(일시 장애 가정), 정기 배치가 DB 커밋 직후 죽는 상황을 만든다
    def skip_immediate() -> object:
        raise RuntimeError("synthetic: immediate purge unavailable")

    monkeypatch.setattr(ctx.container, "purge_user_data", skip_immediate)
    _withdraw(ctx, ctx.user_a)
    monkeypatch.undo()
    assert _user_rows(ctx, ctx.user_a) == (1, 1)  # 아직 남음(논리 삭제 상태)
    monkeypatch.setattr(uc_agent, "_drain", crash)
    with pytest.raises(SystemExit):
        ctx.container.purge_expired_data().execute()
    monkeypatch.undo()
    assert _user_rows(ctx, ctx.user_a) == (0, 0)  # DB 삭제는 커밋됨
    assert file_a.exists() and ctx.checkpoints.deleted == []  # DB 밖 자료는 아직 그대로
    kinds = sorted(t[0] for t in _tasks(ctx))
    assert kinds == ["CHECKPOINT", "FILE"] and all(t[2] == 0 for t in _tasks(ctx))  # 시도 전 상태로 영속

    # '재시작': 같은 DB 를 보는 새 컨테이너(메모리 상태 없음)
    from app.container import Container

    restarted = Container(
        ctx.factory,
        ctx.clock,
        ctx.container.storage,
        ctx.verifier,
        checkpoints=ctx.checkpoints,
        batch_session_factory=ctx.factory,
    )
    code, result = cli_purge.run_purge(restarted)
    assert code == cli_purge.EXIT_OK and result["complete"] is True
    assert (result["files"], result["checkpoints"]) == (1, 1)
    assert not file_a.exists() and ctx.checkpoints.deleted == [thread_id] and _tasks(ctx) == []
    assert _count(ctx, orm.AuditLogModel, action="PURGE", target_type="purge_tasks") == 1  # 잔여분 처리 기록


def test_duplicate_runs_are_safe_and_other_users_data_is_kept(ctx: Ctx) -> None:
    _, thread_a, file_a = _prepare(ctx, ctx.user_a)
    sid_b, thread_b, file_b = _prepare(ctx, ctx.user_b)
    _withdraw(ctx, ctx.user_a)
    for _ in range(3):  # 같은 배치를 여러 번 돌려도 결과가 같다
        report = ctx.container.purge_expired_data().execute()
        assert report.complete and (report.files, report.checkpoints, report.cases, report.sessions) == (
            0,
            0,
            0,
            0,
        )
    assert not file_a.exists() and ctx.checkpoints.deleted == [thread_a]
    # 다른 사용자(B)의 DB 행·파일·체크포인트는 그대로
    assert _user_rows(ctx, ctx.user_b) == (1, 1) and file_b.exists()
    assert thread_b not in ctx.checkpoints.deleted
    r = ctx.client.get(f"{API}/check-sessions/{sid_b}/facts", headers=ctx.auth(ctx.user_b))
    assert r.status_code == 200 and len(r.json()["facts"]) == 3
    assert _tasks(ctx) == []


def test_same_target_is_recorded_once(ctx: Ctx) -> None:
    from app.domain.enums import PurgeTaskKind
    from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

    for _ in range(2):
        with SqlAlchemyUnitOfWork(ctx.factory) as uow:
            n = uow.purge.enqueue_tasks(PurgeTaskKind.FILE, ["k1", "k1", "k2", ""], ctx.clock.now())
            uow.commit()
    assert n == 0 and sorted(t[1] for t in _tasks(ctx)) == ["k1", "k2"]


def test_unconnected_checkpoint_store_is_never_reported_as_purged(
    ctx: Ctx, capsys: pytest.CaptureFixture[str]
) -> None:
    """S4 재현 3번의 반대 + 지시: 미연결을 '파기 완료'로 쓰지 않는다."""
    _, thread_id, file_a = _prepare(ctx, ctx.user_a)
    ctx.container.checkpoints = None  # 연결 안 됨(자료 유무 불명)
    _withdraw(ctx, ctx.user_a)
    assert _user_rows(ctx, ctx.user_a) == (0, 0) and not file_a.exists()  # DB·파일 파기는 막히지 않는다
    assert _tasks(ctx) == [("CHECKPOINT", thread_id, 1, "STORE_NOT_CONNECTED")]
    assert cli_purge.main(ctx.container) == cli_purge.EXIT_PENDING  # 종료 코드 3 = 미완료
    out = json.loads(capsys.readouterr().out)
    assert out["complete"] is False and out["checkpoint_store"] == "NOT_CONNECTED"
    assert (out["checkpoints"], out["pending_checkpoints"]) == (0, 1)
    assert thread_id not in json.dumps(out) and str(ctx.user_a) not in json.dumps(out)  # 식별자 미출력
    # 나중에 저장소를 연결하면 남은 대상을 지우고 완료가 된다
    ctx.container.checkpoints = ctx.checkpoints
    assert cli_purge.main(ctx.container) == cli_purge.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["complete"] is True and out["checkpoint_store"] == "CONNECTED" and out["checkpoints"] == 1
    assert ctx.checkpoints.deleted == [thread_id]


def test_explicitly_unused_checkpoint_store_is_distinguished(ctx: Ctx) -> None:
    _prepare(ctx, ctx.user_a)
    ctx.container.checkpoints = NoCheckpointStore()  # '쓰지 않는다'고 명시
    _withdraw(ctx, ctx.user_a)
    report = ctx.container.purge_expired_data().execute()
    assert report.checkpoint_store == "NOT_USED" and report.complete
    assert report.checkpoints == 0  # 지운 것이 아니라 '지울 것이 없음' → 삭제 건수로 세지 않는다
    assert _tasks(ctx) == [] and ctx.checkpoints.deleted == []


def test_purge_command_reports_error_without_details(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> object:
        raise RuntimeError("secret-looking detail postgresql://user:pw@host/db")

    monkeypatch.setattr(ctx.container, "purge_expired_data", boom)
    assert cli_purge.main(ctx.container) == cli_purge.EXIT_ERROR
    out = capsys.readouterr().out
    assert json.loads(out) == {"complete": False, "error": "PURGE_RUN_FAILED"} and "postgresql://" not in out


def test_langgraph_store_does_not_silently_succeed_without_tables(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'cp.db'}")
    store = LangGraphCheckpointStore(engine)
    with pytest.raises(CheckpointStoreUnavailableError):
        store.delete_thread("t-1")  # 테이블이 없으면 '확인 불가' — 성공으로 치지 않는다
    with engine.begin() as conn:
        for table in LangGraphCheckpointStore.TABLES:
            conn.execute(text(f"CREATE TABLE {table} (thread_id TEXT, payload TEXT)"))
            conn.execute(text(f"INSERT INTO {table} VALUES ('t-1','a'), ('t-2','b')"))  # noqa: S608  # nosec B608 - 고정 목록
    store.delete_thread("t-1")
    with engine.connect() as conn:
        for table in LangGraphCheckpointStore.TABLES:
            found: list[Any] = list(conn.execute(text(f"SELECT thread_id FROM {table}")).scalars())  # noqa: S608  # nosec B608
            assert found == ["t-2"]
    engine.dispose()


def test_checkpoint_settings_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    base = {"database_url": "sqlite://", "jwt_secret": "s" * 40, "storage_dir": Path(".")}
    assert Settings(**base).checkpoint_store is None  # type: ignore[arg-type]
    assert Settings(**base, checkpoint_store="NOT_USED").checkpoint_store == "NOT_USED"  # type: ignore[arg-type]
    for bad in (
        {"checkpoint_store": "MEMORY"},
        {"checkpoint_store": "NOT_USED", "checkpoint_database_url": "x"},
    ):
        with pytest.raises(RuntimeError):
            Settings(**base, **bad)  # type: ignore[arg-type]
    monkeypatch.setenv("HJ_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("HJ_CHECKPOINT_STORE", " not_used ")
    monkeypatch.delenv("HJ_PUBLIC_SIGNUP_ENABLED", raising=False)
    env = Settings.from_env()
    assert env.checkpoint_store == "NOT_USED" and env.public_signup_enabled is False


def test_rls_app_role_cannot_touch_purge_tasks(ctx: Ctx) -> None:
    """RLS 모드 전용: 시민 앱 접속 롤은 파기 대기 기록을 읽지도 쓰지도 못한다."""
    import os

    from sqlalchemy.exc import ProgrammingError

    if not os.environ.get("HJ_TEST_APP_DATABASE_URL"):
        pytest.skip("RLS 모드(HJ_TEST_APP_DATABASE_URL)에서만 실행")
    app_factory = ctx.container._factory
    for sql in ("SELECT count(*) FROM purge_tasks", "DELETE FROM purge_tasks"):
        with app_factory() as s, pytest.raises(ProgrammingError):
            s.execute(text(sql))
