"""시험 계정 발급 명령(v0.11 SUP-01).

공개 회원가입(/auth/signup)은 기본으로 닫혀 있다. 시험·시연에 쓸 계정은 이 명령으로만 만든다.
- 공개 발급 API 는 만들지 않는다. 이 명령은 서버에서, 인증 전용 DB 접속 정보(HJ_AUTH_DATABASE_URL)를
  가진 운영자만 실행할 수 있다(권한 = DB 접속 권한).
- 가입과 '같은' 유스케이스(SignUp)를 쓰므로 이메일 정규화·비밀번호 규칙·Argon2id 해시·감사 기록이 동일하다.
- 비밀번호는 명령행 인자로 받지 않는다(프로세스 목록·셸 기록에 남기 때문). 환경변수 또는 표준입력으로만 받고,
  어디에도 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import uuid

from app.application.use_cases.account import SignUpCommand
from app.container import Container
from app.domain.errors import DomainError

PASSWORD_ENV = "HJ_TEST_ACCOUNT_PASSWORD"  # noqa: S105  # nosec B105 - 환경변수 '이름', 비밀값 아님


def issue_account(
    container: Container, email: str, password: str, display_name: str | None = None
) -> uuid.UUID:
    """계정 1개를 만들고 사용자 ID 를 돌려준다. 공개 가입 스위치와 무관하게 동작한다(운영자 명령)."""
    return container.sign_up().execute(SignUpCommand(email, password, display_name))


def main(argv: list[str] | None = None, container: Container | None = None) -> int:
    parser = argparse.ArgumentParser(description="시험 계정 발급(공개 가입이 닫힌 상태에서 사용)")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default=None)
    args = parser.parse_args(argv)

    password = os.environ.get(PASSWORD_ENV) or ""
    if not password and sys.stdin.isatty():
        password = getpass.getpass("비밀번호(화면에 표시되지 않음): ")
    if not password:
        print(f"비밀번호가 없습니다. 환경변수 {PASSWORD_ENV} 로 전달하세요.", file=sys.stderr)
        return 2
    if container is None:
        from app.main import build_container  # 지연 import: 시험에서는 조립된 컨테이너를 직접 넘긴다

        container = build_container()
    try:
        user_id = issue_account(container, args.email, password, args.display_name)
    except DomainError as exc:
        print(f"발급 실패: {exc.code}", file=sys.stderr)  # 사유 코드만(입력값·비밀번호는 출력하지 않음)
        return 1
    print(f"issued user_id={user_id}")
    return 0
