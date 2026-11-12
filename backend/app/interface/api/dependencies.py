"""FastAPI 의존성: 컨테이너 조회, 인증 사용자 확인.

v0.9
- 시민 API: 시민 토큰 검증 + 계정 상태 확인(탈퇴·휴면 계정의 남은 토큰 즉시 거부).
- AI 내부 API(사실 등록·보고서 초안 제출): 서비스 토큰만 허용(R-01). 시민 토큰이면 403.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.container import Container
from app.domain.errors import AuthenticationError
from app.infrastructure.services import CitizenClaims, InvalidTokenError

_bearer = HTTPBearer(auto_error=False)

# AI 내부 API 권한 범위(scope). 서비스 토큰에 해당 값이 있어야 호출할 수 있다.
SCOPE_FACTS_WRITE = "ai:facts:write"
SCOPE_REPORTS_WRITE = "ai:reports:write"


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.", headers={"WWW-Authenticate": "Bearer"}
    )


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def _ensure_active(container: Container, user_id: uuid.UUID) -> None:
    try:
        container.for_user(user_id).ensure_active_user().execute(user_id)
    except AuthenticationError:
        raise _unauthorized() from None


def citizen_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    container: Container = Depends(get_container),
) -> CitizenClaims:
    """Authorization: Bearer <JWT> 를 검증하고 계정이 ACTIVE 인지 확인한다. 실패 사유는 노출하지 않는다."""
    if credentials is None:
        raise _unauthorized()
    try:
        claims = container.token_verifier.claims(credentials.credentials)
    except InvalidTokenError:
        raise _unauthorized() from None
    _ensure_active(container, claims.user_id)
    return claims


def current_user_id(claims: CitizenClaims = Depends(citizen_claims)) -> uuid.UUID:
    return claims.user_id


def user_container(
    user_id: uuid.UUID = Depends(current_user_id),
    container: Container = Depends(get_container),
) -> Container:
    """v0.8 RLS: 인증된 사용자에 묶인 컨테이너. 이 컨테이너의 DB 작업은 해당 사용자 행만 보인다."""
    return container.for_user(user_id)


# ---------------------------------------------------------------------------
# AI 내부 API(v0.9, R-01)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ServiceCaller:
    service: str
    user_id: uuid.UUID  # 처리 대상 시민(점검 소유자 확인은 유스케이스가 이 값으로 그대로 한다)
    container: Container


def _service_caller(scope: str) -> Callable[..., ServiceCaller]:
    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        container: Container = Depends(get_container),
    ) -> ServiceCaller:
        if credentials is None:
            raise _unauthorized()
        token = credentials.credentials
        try:
            claims = container.service_verifier.verify(token)
        except InvalidTokenError:
            # 시민 토큰으로 내부 API 를 부른 경우는 '권한 없음(403)'으로 구분해 알린다(그 밖은 401)
            try:
                container.token_verifier.claims(token)
            except InvalidTokenError:
                raise _unauthorized() from None
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "이 API 는 AI 내부 서비스만 호출할 수 있습니다."
            ) from None
        if scope not in claims.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "권한 범위가 없습니다.")
        _ensure_active(container, claims.on_behalf_of)
        return ServiceCaller(claims.service, claims.on_behalf_of, container.for_user(claims.on_behalf_of))

    return dependency


facts_writer = _service_caller(SCOPE_FACTS_WRITE)
reports_writer = _service_caller(SCOPE_REPORTS_WRITE)
