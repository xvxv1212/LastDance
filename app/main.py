"""FastAPI 애플리케이션 팩토리.

create_app() 이 Container 를 인자로 받으므로, 테스트에서는 SQLite·임시 저장소로 조립한
컨테이너를 넣어 운영 코드 수정 없이 전체 API 를 검증할 수 있다.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.ports.services import CheckpointStore, KnowledgeBase
from app.application.use_cases.account import LoginLockPolicy
from app.application.use_cases.agent import RetentionPolicy
from app.config import Settings
from app.container import Container
from app.infrastructure.db.engine import build_session_factory
from app.infrastructure.knowledge import JsonAgencyDirectory, LocalKnowledgeBase
from app.infrastructure.pgvector_knowledge import PgVectorKnowledgeBase
from app.infrastructure.services import (
    JwtTokenVerifier,
    LangGraphCheckpointStore,
    LocalFileStorage,
    NoCheckpointStore,
    ServiceTokenVerifier,
    SystemClock,
)
from app.interface.api.error_handlers import register_error_handlers
from app.interface.api.routers import router


def create_app(container: Container, cors_origins: tuple[str, ...] = ()) -> FastAPI:
    app = FastAPI(title="권리채비 AI API", version="0.1.0")
    app.state.container = container
    if cors_origins:
        # 허용 출처를 명시(와일드카드 * 금지)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],  # DELETE: 사건 삭제·회원 탈퇴
            allow_headers=["Authorization", "Content-Type"],
        )
    register_error_handlers(app)
    app.include_router(router)
    return app


def build_container(settings: Settings | None = None) -> Container:
    """환경설정으로 운영 컨테이너를 조립한다. API 서버와 운영자 명령(scripts/)이 같은 조립을 쓴다."""
    settings = settings or Settings.from_env()
    _, factory = build_session_factory(settings.database_url)
    batch_factory = None
    if settings.batch_database_url:
        _, batch_factory = build_session_factory(settings.batch_database_url)
    auth_factory = None
    if settings.auth_database_url:
        _, auth_factory = build_session_factory(settings.auth_database_url)
    knowledge: KnowledgeBase
    if settings.knowledge_database_url:
        knowledge_engine, _ = build_session_factory(settings.knowledge_database_url)
        knowledge = PgVectorKnowledgeBase(knowledge_engine)
    else:
        knowledge = LocalKnowledgeBase.from_json(settings.knowledge_path)
    checkpoints: CheckpointStore | None = None  # None = 연결 안 됨(체크포인트 파기는 대기로 남는다)
    if settings.checkpoint_database_url:
        checkpoint_engine, _ = build_session_factory(settings.checkpoint_database_url)
        checkpoints = LangGraphCheckpointStore(checkpoint_engine)
    elif settings.checkpoint_store == "NOT_USED":
        checkpoints = NoCheckpointStore()
    container = Container(
        session_factory=factory,
        clock=SystemClock(),
        storage=LocalFileStorage(settings.storage_dir),
        token_verifier=JwtTokenVerifier(settings.jwt_secret),
        retention_days=settings.document_retention_days,
        checkpoints=checkpoints,
        knowledge=knowledge,
        agencies=JsonAgencyDirectory.from_json(settings.agencies_path),
        batch_session_factory=batch_factory,
        auth_session_factory=auth_factory,
        service_verifier=ServiceTokenVerifier(settings.service_jwt_secret),
        login_lock=LoginLockPolicy(settings.login_max_failures, settings.login_lock_minutes),
        retention=RetentionPolicy(
            deleted_case_grace_days=settings.deleted_case_grace_days,
            unconverted_session_days=settings.unconverted_session_days,
            closed_case_days=settings.closed_case_days,
            audit_log_days=settings.audit_log_days,
        ),
        access_token_minutes=settings.access_token_minutes,
        public_signup_enabled=settings.public_signup_enabled,
    )
    return container


def build_production_app() -> FastAPI:
    """운영 진입점: `uvicorn app.main:build_production_app --factory`"""
    settings = Settings.from_env()
    return create_app(build_container(settings), settings.cors_origins)
