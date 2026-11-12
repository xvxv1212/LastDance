"""SQLAlchemy Unit of Work: 하나의 DB 트랜잭션 안에서 모든 저장소를 묶어 제공한다.

v0.8 RLS 연결: 요청한 사용자(acting_user_id)가 있으면, PostgreSQL 에서는 트랜잭션이 시작될 때마다
`set_config('app.user_id', <사용자>, true)` 를 실행한다. 세 번째 인자 true = '이 트랜잭션에서만' 유효하므로
커넥션 풀에서 다음 요청으로 값이 새지 않고, 커밋·롤백 뒤 새 트랜잭션에도 다시 설정된다.
RLS 정책(db/rls_policies.sql)은 이 값으로 행을 거른다. SQLite(테스트)는 RLS 가 없어 건너뛴다.
"""

from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.application.ports.services import UnitOfWork
from app.infrastructure.db import repositories as repos


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(
        self, session_factory: sessionmaker[Session], acting_user_id: uuid.UUID | None = None
    ) -> None:
        self._factory = session_factory
        self._acting_user_id = acting_user_id
        self._session: Session | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        s = self._session = self._factory()
        if self._acting_user_id is not None and s.get_bind().dialect.name == "postgresql":
            user = str(self._acting_user_id)

            def _bind_user(_s: Session, _tx: SessionTransaction, conn: Connection, *_: Any) -> None:
                conn.execute(text("SELECT set_config('app.user_id', :u, true)"), {"u": user})

            event.listen(s, "after_begin", _bind_user)
        # 같은 세션(=같은 트랜잭션)을 공유하는 저장소들을 생성
        self.users = repos.SqlUserRepository(s)
        self.consents = repos.SqlConsentRepository(s)
        self.sessions = repos.SqlCheckSessionRepository(s)
        self.documents = repos.SqlDocumentRepository(s)
        self.facts = repos.SqlFactRepository(s)
        self.reports = repos.SqlReportRepository(s)
        self.cases = repos.SqlCaseRepository(s)
        self.case_records = repos.SqlCaseRecordRepository(s)
        self.audit_logs = repos.SqlAuditLogRepository(s)
        self.purge = repos.SqlPurgeRepository(s)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        super().__exit__(exc_type, exc, tb)  # 미커밋 변경 롤백
        if self._session is not None:
            self._session.close()
            self._session = None

    def _require(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork 는 with 블록 안에서만 사용할 수 있습니다.")
        return self._session

    def commit(self) -> None:
        self._require().commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
