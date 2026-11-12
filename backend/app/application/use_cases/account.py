"""회원 계정 유스케이스(v0.9): 회원가입, 로그인, 계정 상태 확인, 회원 탈퇴.

팀 결정(2026.11.10)
- 로그인: 이메일 + 비밀번호. 비밀번호는 Argon2id 일방향 해시로만 저장(안전성 확보조치 기준 제7조).
  연속 실패 시 일정 시간 잠금(같은 기준 제5조 취지). 납품 시 정부 통합인증(Any-ID) 도입 검토.
- 탈퇴: 탈퇴 요청 → 즉시 접근 차단 → 개인정보 삭제·파기. 가입보다 어렵게 만들지 않는다
  (개인정보 보호법 제38조④). 본인 확인은 '비밀번호 재입력'으로 한다.
- 동의 이력·감사 로그는 영구 보존하지 않는다: 동의 이력은 탈퇴 즉시 파기, 감사 로그는 보관기간(잠정 90일).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.application.ports.services import Clock, PasswordHasher, UnitOfWork
from app.application.use_cases.common import audit
from app.domain.entities import User, new_id
from app.domain.enums import AuditAction, UserRole, UserStatus
from app.domain.errors import (
    AuthenticationError,
    DuplicateError,
    LoginLockedError,
    ReauthRequiredError,
)
from app.domain.policies import CredentialPolicy


@dataclass(frozen=True)
class LoginLockPolicy:
    max_failures: int = 5
    lock_minutes: int = 10


# ===========================================================================
# 회원가입
# ===========================================================================
@dataclass(frozen=True)
class SignUpCommand:
    email: str
    password: str
    display_name: str | None = None


class SignUp:
    """이메일·비밀번호로 가입한다. 가입 시점엔 사용자 ID 가 없어 인증 전용 접속(app_auth)으로 실행한다."""

    def __init__(self, uow: UnitOfWork, clock: Clock, hasher: PasswordHasher) -> None:
        self._uow = uow
        self._clock = clock
        self._hasher = hasher

    def execute(self, cmd: SignUpCommand) -> uuid.UUID:
        email = CredentialPolicy.normalize_email(cmd.email)
        CredentialPolicy.validate_password(cmd.password, email)
        password_hash = self._hasher.hash(cmd.password)  # 트랜잭션 밖에서(해시는 일부러 느리다)
        now = self._clock.now()
        with self._uow as uow:
            if uow.users.find_by_email(email) is not None:
                raise DuplicateError("이미 가입된 이메일입니다.")
            user = User(
                id=new_id(),
                email=email,
                password_hash=password_hash,
                display_name=cmd.display_name,
                role=UserRole.CITIZEN,  # MVP: 시민만. 상담사·관리자 계정은 만들지 않는다
                status=UserStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            uow.users.add(user)  # 동시 가입은 UNIQUE 위반 → DuplicateError
            audit(uow, self._clock, user.id, AuditAction.CREATE, "users", user.id, None)
            uow.commit()
            return user.id


# ===========================================================================
# 로그인
# ===========================================================================
@dataclass(frozen=True)
class LoginCommand:
    email: str
    password: str


class Login:
    """이메일·비밀번호 확인. 실패 사유(이메일 없음/비밀번호 틀림)는 구분하지 않고 같은 응답을 준다."""

    def __init__(self, uow: UnitOfWork, clock: Clock, hasher: PasswordHasher, lock: LoginLockPolicy) -> None:
        self._uow = uow
        self._clock = clock
        self._hasher = hasher
        self._lock = lock

    def execute(self, cmd: LoginCommand) -> uuid.UUID:
        fail = AuthenticationError("이메일 또는 비밀번호가 올바르지 않습니다.")
        try:
            email = CredentialPolicy.normalize_email(cmd.email)
        except Exception:
            raise fail from None
        now = self._clock.now()
        with self._uow as uow:
            user = uow.users.find_by_email(email)
            if user is None:
                self._hasher.dummy_verify(cmd.password)
                raise fail
            if user.is_locked(now):
                raise LoginLockedError("로그인 실패가 반복되어 잠시 잠겼습니다. 잠시 후 다시 시도해 주세요.")
            if user.password_hash is None or not self._hasher.verify(user.password_hash, cmd.password):
                user.record_login_failure(
                    now, self._lock.max_failures, timedelta(minutes=self._lock.lock_minutes)
                )
                uow.users.save(user)
                uow.commit()  # 실패 횟수는 실패 응답과 함께 남아야 한다
                raise fail
            if not user.is_active:
                raise fail
            user.record_login_success(now)
            uow.users.save(user)
            uow.commit()
            return user.id


# ===========================================================================
# 모든 요청: 계정 상태 확인
# ===========================================================================
class EnsureActiveUser:
    """토큰이 유효해도 계정이 ACTIVE 가 아니면(탈퇴·휴면·삭제) 거부한다.

    JWT 는 서버가 회수할 수 없으므로, 탈퇴 직후 남은 토큰을 막으려면 요청마다 DB 상태를 봐야 한다.
    응답은 '인증 필요(401)' 하나로 통일해 계정 존재 여부를 드러내지 않는다.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, user_id: uuid.UUID) -> None:
        with self._uow as uow:
            user = uow.users.get(user_id)
            if user is None or not user.is_active:
                raise AuthenticationError("인증이 필요합니다.")


# ===========================================================================
# 회원 탈퇴
# ===========================================================================
@dataclass(frozen=True)
class WithdrawCommand:
    user_id: uuid.UUID
    password: str  # 본인 재확인


class WithdrawAccount:
    """탈퇴 요청 처리(사용자 권한 트랜잭션). 실제 파기는 직후 PurgeUserData 가 수행한다.

    한 트랜잭션에서: 비밀번호 재확인 → 계정 WITHDRAWN + 식별정보 삭제 → 점검·사건 논리 삭제 → 감사 기록.
    이 커밋만으로도 접근은 즉시 막히고(EnsureActiveUser), 파기가 실패해도 다음 배치가 다시 처리한다.
    비밀번호 재확인 실패도 로그인 실패 횟수에 넣는다(탈취한 토큰으로 비밀번호를 맞혀 보는 것을 막음).
    """

    def __init__(self, uow: UnitOfWork, clock: Clock, hasher: PasswordHasher, lock: LoginLockPolicy) -> None:
        self._uow = uow
        self._clock = clock
        self._hasher = hasher
        self._lock = lock

    def execute(self, cmd: WithdrawCommand) -> None:
        now = self._clock.now()
        with self._uow as uow:
            user = uow.users.get(cmd.user_id)
            if user is None or not user.is_active:
                raise AuthenticationError("인증이 필요합니다.")
            if user.is_locked(now):
                raise LoginLockedError("확인 실패가 반복되어 잠시 잠겼습니다. 잠시 후 다시 시도해 주세요.")
            if user.password_hash is None or not self._hasher.verify(user.password_hash, cmd.password):
                user.record_login_failure(
                    now, self._lock.max_failures, timedelta(minutes=self._lock.lock_minutes)
                )
                uow.users.save(user)
                uow.commit()
                raise ReauthRequiredError("비밀번호가 올바르지 않습니다.")
            user.withdraw(now)
            uow.users.save(user)
            sessions = uow.sessions.soft_delete_by_owner(user.id, now)
            cases = uow.cases.soft_delete_by_owner(user.id, now)
            audit(
                uow,
                self._clock,
                user.id,
                AuditAction.DELETE,
                "users",
                user.id,
                {"reason": "WITHDRAWAL", "sessions": sessions, "cases": cases},
            )
            uow.commit()
