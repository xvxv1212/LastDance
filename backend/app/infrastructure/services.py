"""DB 외 포트 구현: 시계, 로컬 파일 저장소, JWT 토큰 검증."""

from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import argon2
import jwt
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.application.ports.services import (
    CheckpointStore,
    Clock,
    ExternalModelClient,
    FileStorage,
    ModelNotConfiguredError,
    ModelResult,
    PasswordHasher,
)


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


class LocalFileStorage(FileStorage):
    """개발용 로컬 디스크 저장소.

    보안 고려:
    - 사용자 파일명을 경로에 쓰지 않고 랜덤 키만 사용 → 경로 조작(Path Traversal) 차단
    - 파일 권한 0600 → 같은 서버의 다른 계정이 읽지 못함
    - 운영에서는 서버측 암호화(SSE)가 되는 객체 저장소 구현체로 교체한다(OCP).
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        if not storage_key.isalnum():
            raise ValueError("잘못된 저장소 키입니다.")
        path = (self._root / storage_key).resolve()
        if path.parent != self._root:
            raise ValueError("저장소 밖의 경로입니다.")
        return path

    def put(self, content: bytes) -> str:
        key = secrets.token_hex(32)  # 256bit 랜덤
        path = self._path(key)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        return key

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True)
class CitizenClaims:
    user_id: uuid.UUID
    issued_at: datetime  # 로그인 시각(탈퇴 등 민감 작업의 '최근 본인 확인' 판단에 쓴다)


class JwtTokenVerifier:
    """시민용 Bearer 토큰(HS256 JWT)을 검증하고 사용자 ID 를 돌려준다.

    알고리즘을 명시적으로 고정하여 'alg=none' 공격을 막고, exp(만료)·sub 를 필수로 요구한다.
    v0.9: aud(대상) 가 붙은 토큰(=AI 내부 서비스 토큰)은 시민 토큰으로 받지 않는다.
    """

    def __init__(self, secret: str, issuer: str = "gwonri-chaebi") -> None:
        if len(secret) < 32:
            raise ValueError("JWT 비밀키는 32자 이상이어야 합니다.")
        self._secret = secret
        self._issuer = issuer

    def claims(self, token: str) -> CitizenClaims:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["exp", "sub", "iss"]},
            )
            if (
                "aud" in claims
            ):  # PyJWT 는 audience 인자가 없으면 aud 를 거부하지만, 명시적으로 한 번 더 막는다
                raise InvalidTokenError("시민 토큰이 아닙니다.")
            iat = claims.get("iat")
            issued = (
                datetime.fromtimestamp(int(iat), UTC) if iat is not None else datetime.min.replace(tzinfo=UTC)
            )
            return CitizenClaims(uuid.UUID(str(claims["sub"])), issued)
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise InvalidTokenError("유효하지 않은 인증 토큰입니다.") from exc

    def verify(self, token: str) -> uuid.UUID:
        return self.claims(token).user_id

    def issue(self, user_id: uuid.UUID, expires_at: datetime, issued_at: datetime | None = None) -> str:
        """토큰 발급(로그인 성공 시·테스트용). iat = 로그인 시각."""
        iat = issued_at or datetime.now(UTC)
        return jwt.encode(
            {"sub": str(user_id), "iss": self._issuer, "exp": expires_at, "iat": iat},
            self._secret,
            algorithm="HS256",
        )


@dataclass(frozen=True)
class ServiceClaims:
    service: str  # 호출한 내부 서비스 이름(예: ai-agent)
    on_behalf_of: uuid.UUID  # 이 호출이 처리하는 시민(점검 소유자 확인에 그대로 쓴다)
    scopes: frozenset[str]


class ServiceTokenVerifier:
    """AI 내부 API 전용 서비스 토큰(v0.9, R-01).

    - 시민 토큰과 '다른 비밀키'(HJ_SERVICE_JWT_SECRET)로 서명 → 시민 비밀키가 새어도 서비스 권한은 없다.
    - aud=gwonri-ai-internal 필수 → 서비스 토큰을 시민 API 에 쓰면 시민 검증기가 거부한다.
    - scope(권한 범위)·obo(대상 시민)·exp(짧게, 기본 15분) 필수.
    - 비밀키를 설정하지 않으면 AI 내부 API 는 모두 거부된다(fail-closed).
    """

    AUDIENCE = "gwonri-ai-internal"
    MAX_LIFETIME_SECONDS = 15 * 60

    def __init__(self, secret: str | None, issuer: str = "gwonri-chaebi") -> None:
        if secret is not None and len(secret) < 32:
            raise ValueError("서비스 JWT 비밀키는 32자 이상이어야 합니다.")
        self._secret = secret
        self._issuer = issuer

    @property
    def enabled(self) -> bool:
        return self._secret is not None

    def verify(self, token: str) -> ServiceClaims:
        if self._secret is None:
            raise InvalidTokenError("서비스 토큰이 설정되지 않았습니다.")
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self.AUDIENCE,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "obo", "scope"]},
            )
            if int(claims["exp"]) - int(claims["iat"]) > self.MAX_LIFETIME_SECONDS:
                raise InvalidTokenError("서비스 토큰 유효기간이 너무 깁니다.")
            scopes = claims["scope"]
            if not isinstance(scopes, str):
                raise InvalidTokenError("scope 형식 오류")
            return ServiceClaims(str(claims["sub"]), uuid.UUID(str(claims["obo"])), frozenset(scopes.split()))
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise InvalidTokenError("유효하지 않은 서비스 토큰입니다.") from exc

    def issue(
        self, service: str, on_behalf_of: uuid.UUID, scopes: list[str], lifetime_seconds: int = 300
    ) -> str:
        """AI 실행부(LangGraph 워커)가 같은 비밀키로 발급한다. 이 메서드는 워커·테스트용."""
        if self._secret is None:
            raise InvalidTokenError("서비스 토큰이 설정되지 않았습니다.")
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": service,
                "iss": self._issuer,
                "aud": self.AUDIENCE,
                "obo": str(on_behalf_of),
                "scope": " ".join(scopes),
                "iat": now,
                "exp": now + timedelta(seconds=lifetime_seconds),
            },
            self._secret,
            algorithm="HS256",
        )


class CheckpointStoreUnavailableError(RuntimeError):
    """체크포인트 저장소에 닿지 못했거나 자료 유무를 확인할 수 없음."""


class NoCheckpointStore(CheckpointStore):
    """'체크포인트 저장소를 쓰지 않는 배포'임을 명시하는 구현(v0.11).

    지울 자료가 없으므로 아무것도 하지 않는다.

    설정에서 HJ_CHECKPOINT_STORE=NOT_USED 로 '명시'했을 때만 쓴다. 설정을 비워 둔 것과 다르다.
    """

    in_use = False

    def delete_thread(self, thread_id: str) -> None:
        return None


class LangGraphCheckpointStore(CheckpointStore):
    """LangGraph PostgresSaver 가 만드는 체크포인트 테이블에서 thread 단위로 삭제한다.

    체크포인트 원본은 라이브러리 관리 테이블이라 업무 FK(CASCADE)로 지워지지 않는다.
    따라서 파기 배치가 agent_threads 기준으로 이 어댑터를 호출해 따로 지운다(DB-04·05).
    """

    TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def delete_thread(self, thread_id: str) -> None:
        existing = set(inspect(self._engine).get_table_names())
        if not existing.intersection(self.TABLES):
            # v0.11: 체크포인트 테이블이 하나도 안 보이면 '지울 것이 없다'가 아니라 '확인 불가'다
            # (잘못된 DB 에 붙었을 수 있다). 성공으로 처리하지 않고 대기 작업으로 남긴다.
            raise CheckpointStoreUnavailableError("체크포인트 테이블을 찾을 수 없습니다.")
        with self._engine.begin() as conn:
            for table in self.TABLES:
                if table in existing:  # 테이블명은 고정 목록에서만 가져오므로 SQL 주입 위험 없음
                    conn.execute(text(f"DELETE FROM {table} WHERE thread_id = :tid"), {"tid": thread_id})  # noqa: S608  # nosec B608


class DisabledModelClient(ExternalModelClient):
    """외부 모델 미설정 상태의 기본 구현. 설정 전에는 어떤 데이터도 외부로 나가지 않는다."""

    def complete(self, node_name: str, masked_text: str) -> ModelResult:
        raise ModelNotConfiguredError("외부 모델 API 가 설정되지 않았습니다.")


class Argon2PasswordHasher(PasswordHasher):
    """Argon2id 해시(argon2-cffi). 기본값은 라이브러리 권장 매개변수(RFC 9106 기반).

    시험에서는 속도를 위해 time_cost·memory_cost 를 낮춘 인스턴스를 쓴다(운영 값 아님).
    """

    def __init__(self, time_cost: int | None = None, memory_cost: int | None = None) -> None:
        default = argon2.PasswordHasher()
        self._ph = argon2.PasswordHasher(
            time_cost=time_cost if time_cost is not None else default.time_cost,
            memory_cost=memory_cost if memory_cost is not None else default.memory_cost,
            type=argon2.Type.ID,
        )
        self._dummy = self._ph.hash("dummy-password-for-timing")

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return bool(self._ph.verify(password_hash, password))
        except (argon2.exceptions.VerificationError, argon2.exceptions.InvalidHashError):
            return False

    def dummy_verify(self, password: str) -> None:
        self.verify(self._dummy, password)
