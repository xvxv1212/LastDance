"""도메인 예외.

유스케이스는 HTTP 상태코드를 모른다. 인터페이스 계층(error_handlers)이
이 예외들을 HTTP 응답으로 '번역'한다 → 계층 간 결합을 끊는다(DIP).
"""


class DomainError(Exception):
    """모든 도메인 예외의 부모."""

    code = "DOMAIN_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    """대상이 없거나, 요청자에게 존재를 알려서는 안 되는 경우."""

    code = "NOT_FOUND"


class PermissionDeniedError(DomainError):
    """본인 소유가 아닌 자원에 접근."""

    code = "PERMISSION_DENIED"


class ValidationError(DomainError):
    """업무 규칙 위반(입력값·상태 전이 등)."""

    code = "VALIDATION_ERROR"


class ConflictError(DomainError):
    """현재 상태와 충돌(이미 전환된 세션 재전환 등)."""

    code = "CONFLICT"


class DuplicateError(ConflictError):
    """유일성 제약 위반(예: 같은 점검의 동시 사건 전환). 저장소가 DB 오류를 이 예외로 번역한다."""

    code = "DUPLICATE"


class ConsentRequiredError(DomainError):
    """필요한 동의가 없음."""

    code = "CONSENT_REQUIRED"


class AuthenticationError(DomainError):
    """로그인 실패·탈퇴/비활성 계정(v0.9). 응답은 401."""

    code = "AUTHENTICATION_REQUIRED"


class ReauthRequiredError(DomainError):
    """탈퇴 등 되돌릴 수 없는 작업 전 비밀번호 재확인 실패(v0.9). 응답은 403(로그인 상태는 유지)."""

    code = "REAUTH_FAILED"


class LoginLockedError(DomainError):
    """연속 실패로 잠긴 계정(v0.9). 응답은 429."""

    code = "LOGIN_LOCKED"
