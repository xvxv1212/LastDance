"""도메인 예외 → HTTP 응답 변환. 유스케이스는 HTTP 를 모르고, 이 파일만 HTTP 를 안다."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain import errors as err

logger = logging.getLogger(__name__)

_STATUS = {
    err.NotFoundError: 404,
    err.PermissionDeniedError: 404,  # 존재 여부 은닉
    err.ValidationError: 422,
    err.ConflictError: 409,
    err.ConsentRequiredError: 428,  # Precondition Required
    err.AuthenticationError: 401,  # v0.9: 로그인 실패·탈퇴 계정
    err.ReauthRequiredError: 403,  # v0.9: 탈퇴 전 비밀번호 재확인 실패
    err.LoginLockedError: 429,  # v0.9: 연속 실패 잠금
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(err.DomainError)
    async def _domain(_: Request, exc: err.DomainError) -> JSONResponse:
        code = next((s for t, s in _STATUS.items() if isinstance(exc, t)), 400)
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # 스택트레이스·SQL 문은 서버 로그에만 남기고 사용자에게는 노출하지 않는다.
        logger.exception("처리되지 않은 오류", exc_info=exc)
        return JSONResponse(
            {"code": "INTERNAL_ERROR", "message": "일시적인 오류가 발생했습니다."}, status_code=500
        )
