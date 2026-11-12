"""DB 엔진·세션 팩토리 생성."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def build_session_factory(database_url: str, echo: bool = False) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(database_url, echo=echo, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # SQLite 는 기본적으로 FK 를 검사하지 않으므로 테스트에서도 운영과 같은 무결성을 강제한다.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # type: ignore[no-untyped-def]
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine, sessionmaker(bind=engine, expire_on_commit=False)
