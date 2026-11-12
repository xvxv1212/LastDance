"""시험 DB 초기화 보호: pytest가 만든 새 임시 SQLite 파일만 허용한다.

외부 PostgreSQL/RLS 대상의 생성·소유·폐기 이력을 증명하는 준비 도구가 없으므로
환경 변수 URL은 모두 차단한다. DB 이름, localhost, 확인 플래그만으로 격리를 추정하지 않는다.
이 검사는 URL을 출력하거나 DB에 연결하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


class UnsafeTestDatabaseError(RuntimeError):
    """시험 대상의 격리가 증명되지 않아 초기화를 차단했다."""


def isolated_database_url(tmp_path: Path, environ: Mapping[str, str]) -> str:
    """엔진 생성 전에 호출한다. tmp_path는 pytest의 시험별 임시 폴더여야 한다."""
    if "HJ_TEST_DATABASE_URL" in environ or "HJ_TEST_APP_DATABASE_URL" in environ:
        raise UnsafeTestDatabaseError(
            "외부 시험 DB의 격리를 확인할 수 없어 연결·초기화를 차단했습니다. "
            "HJ_TEST_DATABASE_URL과 HJ_TEST_APP_DATABASE_URL을 모두 해제하면 "
            "pytest 임시 SQLite에서 실행됩니다. PostgreSQL/RLS 시험은 격리 대상 증명 전 BLOCKED입니다."
        )

    if not tmp_path.is_absolute() or not tmp_path.is_dir() or tmp_path.is_symlink():
        raise UnsafeTestDatabaseError("pytest가 만든 절대경로 임시 폴더만 사용할 수 있습니다.")
    database_path = tmp_path / "test.db"
    if database_path.exists() or database_path.is_symlink():
        raise UnsafeTestDatabaseError("이미 있는 파일·링크를 시험 DB로 초기화할 수 없습니다.")
    return f"sqlite:///{database_path}"
