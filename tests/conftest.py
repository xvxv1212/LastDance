"""테스트 공통 준비물: SQLite 임시 DB + 고정 시계 + 임시 파일 저장소로 앱을 조립한다.

v0.6: 공식 지식(벡터 지식 DB)과 지원기관 목록은 업무 DB 밖에 있으므로 DB 에 넣지 않고
메모리 구현(LocalKnowledgeBase, JsonAgencyDirectory)으로 주입한다.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.ports.services import (
    CheckpointStore,
    Clock,
    ExternalModelClient,
    ModelResult,
)
from app.container import Container
from app.domain import enums
from app.domain.entities import SupportAgency
from app.infrastructure.db import models as orm
from app.infrastructure.db.base import Base
from app.infrastructure.db.engine import build_session_factory
from app.infrastructure.knowledge import (
    JsonAgencyDirectory,
    KnowledgeChunk,
    KnowledgeSource,
    LocalKnowledgeBase,
)
from app.infrastructure.services import (
    Argon2PasswordHasher,
    JwtTokenVerifier,
    LocalFileStorage,
    ServiceTokenVerifier,
)
from app.interface.api.dependencies import SCOPE_FACTS_WRITE, SCOPE_REPORTS_WRITE
from app.main import create_app

SECRET = "test-secret-key-that-is-at-least-32-chars!!"
SERVICE_SECRET = "test-service-secret-different-from-citizen-key"
PASSWORD = "Synthetic-pass-2026"  # 시험 계정 비밀번호(합성)
# 시험 속도용 저비용 Argon2id(운영은 라이브러리 기본값)
HASHER = Argon2PasswordHasher(time_cost=1, memory_cost=8 * 1024)
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


class FixedClock(Clock):
    """테스트 시계. 파기 배치 시험에서 시간을 앞으로 돌릴 수 있다."""

    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current


class FakeModelClient(ExternalModelClient):
    """외부 모델 대역: 실제 호출 횟수와 전송된 텍스트를 기록한다(외부 호출 0회 검증용)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, node_name: str, masked_text: str) -> ModelResult:
        self.calls.append(masked_text)
        return ModelResult(text="{}", model_name="fake-llm", input_tokens=10, output_tokens=5)


class FakeCheckpointStore(CheckpointStore):
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


@dataclass
class Ctx:
    client: TestClient
    verifier: JwtTokenVerifier
    user_a: uuid.UUID
    user_b: uuid.UUID
    chunk_id: str
    agency_code: str
    container: Container
    factory: Any
    clock: FixedClock
    model: FakeModelClient
    checkpoints: FakeCheckpointStore
    files_dir: Path
    service: ServiceTokenVerifier

    def auth(self, user_id: uuid.UUID) -> dict[str, str]:
        # iat = 시험 시계 기준(탈퇴의 '최근 로그인' 판단이 시험 시계를 쓰므로)
        # (JWT 라이브러리는 '미래 iat' 를 거부하므로 시험 시계가 앞서 있으면 실제 시각을 쓴다)
        token = self.verifier.issue(
            user_id, datetime.now(UTC) + timedelta(minutes=5), min(self.clock.now(), datetime.now(UTC))
        )
        return {"Authorization": f"Bearer {token}"}

    def svc(
        self, user_id: uuid.UUID, scopes: tuple[str, ...] = (SCOPE_FACTS_WRITE, SCOPE_REPORTS_WRITE)
    ) -> dict[str, str]:
        """v0.9: AI 내부 API(사실 등록·보고서 제출)용 서비스 토큰."""
        token = self.service.issue("ai-agent", user_id, list(scopes))
        return {"Authorization": f"Bearer {token}"}


RLS_SQL = Path(__file__).resolve().parents[1] / "db" / "rls_policies.sql"


def _apply_rls(engine: Any, app_role: str) -> None:
    """RLS 모드: 정책을 켜고 앱 롤에 운영과 같은 최소 권한만 준다(db/roles.sql 과 같은 내용)."""
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(RLS_SQL.read_text(encoding="utf-8"))
            # purge_tasks(v0.11)는 파기 전용 접속만 쓴다. 시민 앱 롤에는 어떤 권한도 주지 않는다
            tables = [
                t.name for t in Base.metadata.sorted_tables if t.name not in ("audit_logs", "purge_tasks")
            ]
            cur.execute(f'GRANT SELECT, INSERT, UPDATE ON {", ".join(tables)} TO "{app_role}"')
            cur.execute(f'GRANT INSERT ON audit_logs TO "{app_role}"')
        raw.commit()
    finally:
        raw.close()


@pytest.fixture()
def ctx(tmp_path: Path) -> Iterator[Ctx]:
    # 기본은 SQLite. HJ_TEST_DATABASE_URL 을 주면 실제 PostgreSQL 에서 같은 테스트를 돌린다.
    url = os.environ.get("HJ_TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    engine, factory = build_session_factory(url)
    if engine.dialect.name == "postgresql":
        # v0.6: 업무 DB 에는 vector 컬럼이 없으므로 pgvector 확장이 필요 없다
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # v0.8 RLS 모드: HJ_TEST_APP_DATABASE_URL(비관리자·NOBYPASSRLS 롤)을 주면 앱은 그 계정으로 접속하고
    # RLS 정책이 켜진다. 테이블 생성·시험용 직접 조회·파기 배치는 위의 관리자 접속(factory)을 쓴다.
    app_url = os.environ.get("HJ_TEST_APP_DATABASE_URL")
    app_engine = None
    app_factory = factory
    if app_url and engine.dialect.name == "postgresql":
        app_engine, app_factory = build_session_factory(app_url)
        _apply_rls(engine, app_engine.url.username or "")

    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    chunk_id, agency_code = "fitness-standard#0", "CCN_1372"
    with factory() as s:
        for uid, mail in ((user_a, "a@example.com"), (user_b, "b@example.com")):
            s.add(
                orm.UserModel(
                    id=uid,
                    email=mail,
                    password_hash=HASHER.hash(PASSWORD),
                    role=enums.UserRole.CITIZEN,
                    status=enums.UserStatus.ACTIVE,
                )
            )
        s.commit()

    verifier = JwtTokenVerifier(SECRET)
    service = ServiceTokenVerifier(SERVICE_SECRET)
    clock, model, checkpoints = FixedClock(), FakeModelClient(), FakeCheckpointStore()
    # 합성 지식 데이터(실제 고시 원문 아님): 인용 검증 흐름만 확인한다
    knowledge = LocalKnowledgeBase(
        [
            KnowledgeSource(
                id="fitness-standard",
                source_type=enums.OfficialSourceType.DISPUTE_STANDARD,
                title="소비자분쟁해결기준(테스트용)",
                issuer="공정거래위원회",
                url="https://www.law.go.kr/",
                version_label="test",
                effective_date=date(2025, 1, 1),
            )
        ],
        [
            KnowledgeChunk(
                id=chunk_id,
                source_id="fitness-standard",
                chunk_index=0,
                article_ref="별표 2",
                content="체육시설업 이용 중 해지 시 환급 기준 …",
            )
        ],
    )
    agencies = JsonAgencyDirectory(
        [
            SupportAgency(
                code=agency_code,
                name="1372 소비자상담센터",
                agency_type="소비자상담",
                phone="1372",
                url="https://www.ccn.go.kr/",
                is_active=True,
            )
        ]
    )
    container = Container(
        app_factory,
        clock,
        LocalFileStorage(tmp_path / "files"),
        verifier,
        model_client=model,
        checkpoints=checkpoints,
        knowledge=knowledge,
        agencies=agencies,
        batch_session_factory=factory,
        service_verifier=service,
        password_hasher=HASHER,
        # v0.11 SUP-01: 운영 기본값은 닫힘. 기존 가입 시험은 '명시적으로 켠' 상태에서만 돈다.
        public_signup_enabled=True,
    )
    with TestClient(create_app(container)) as client:
        yield Ctx(
            client,
            verifier,
            user_a,
            user_b,
            chunk_id,
            agency_code,
            container,
            factory,
            clock,
            model,
            checkpoints,
            tmp_path / "files",
            service,
        )
    if app_engine is not None:
        app_engine.dispose()
    engine.dispose()


def agree_all(ctx: Ctx, user_id: uuid.UUID) -> None:
    for t in ("PRIVACY_REQUIRED", "SENSITIVE_DOCUMENT"):
        r = ctx.client.post(
            "/api/v1/consents",
            headers=ctx.auth(user_id),
            json={"consent_type": t, "policy_version": "2026-11-v1", "agreed": True},
        )
        assert r.status_code == 201, r.text
