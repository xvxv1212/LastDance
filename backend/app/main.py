"""FastAPI 애플리케이션 팩토리.

create_app() 이 Container 를 인자로 받으므로, 테스트에서는 SQLite·임시 저장소로 조립한
컨테이너를 넣어 운영 코드 수정 없이 전체 API 를 검증할 수 있다.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import contracts
from app.application.ports.services import CheckpointStore, KnowledgeBase
from app.application.use_cases.account import LoginLockPolicy
from app.application.use_cases.agent import RetentionPolicy
from app.config import Settings
from app.container import Container
from app.infrastructure.citation_approval import ApprovedKnowledgeBase
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
from app.legacy_settings import get_settings


def _include_legacy_routes(app: FastAPI) -> None:
    """LastDance의 기존 상태 확인과 파일 미저장 PDF 추출 계약을 보존한다."""
    app.include_router(contracts.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}


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
    _include_legacy_routes(app)
    return app


def create_legacy_app() -> FastAPI:
    """기존 app.main:app 진입점은 DB 설정 없이 상태 확인·PDF 추출만 제공한다."""
    legacy = FastAPI(
        title="권리채비 AI API",
        description="헬스장·필라테스 회원권 해지·환불 상황의 사실 정리와 상담 준비를 돕는 API. "
        "법률 결론이나 환불액을 판정하지 않는다.",
        version="0.1.0",
    )
    legacy.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _include_legacy_routes(legacy)
    return legacy


# 기존 LastDance 실행 명령 유지. 전체 API는 명시적 컨테이너/설정으로만 조립한다.
app = create_legacy_app()


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
    # 저장 성공·서버 원문 일치 검증과 팀 인용 승인을 구분한다.
    knowledge = ApprovedKnowledgeBase.from_file(knowledge, settings.knowledge_approval_path)
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
