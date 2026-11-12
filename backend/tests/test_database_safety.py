"""운영 DB 보호 회귀시험. 모든 거부 시험은 DB 연결 없이 실행한다."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests import conftest as fixtures
from tests.db_safety import UnsafeTestDatabaseError, isolated_database_url


def test_only_fresh_pytest_sqlite_path_is_allowed(tmp_path: Path) -> None:
    assert isolated_database_url(tmp_path, {}) == f"sqlite:///{tmp_path / 'test.db'}"
    assert not (tmp_path / "test.db").exists()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://app:secret-value@db.example.test/production",
        "postgresql+psycopg://app:secret-value@localhost/test_database",
        "sqlite:////var/lib/application/test.db",
        "sqlite:///:memory:",
        "",
    ],
    ids=["remote", "loopback-test-name", "existing-sqlite", "memory-override", "empty"],
)
def test_external_database_url_is_blocked_without_echoing_value(tmp_path: Path, url: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError, match="격리를 확인할 수 없어") as exc:
        isolated_database_url(tmp_path, {"HJ_TEST_DATABASE_URL": url})
    assert "secret-value" not in str(exc.value)
    if url:
        assert url not in str(exc.value)


@pytest.mark.parametrize("with_admin", [False, True], ids=["app-only", "matching-admin"])
def test_rls_url_needs_proven_isolation_even_when_target_matches(tmp_path: Path, with_admin: bool) -> None:
    environ = {"HJ_TEST_APP_DATABASE_URL": "postgresql://app:secret-value@localhost/test_database"}
    if with_admin:
        environ["HJ_TEST_DATABASE_URL"] = "postgresql://admin:secret-value@localhost/test_database"
    with pytest.raises(UnsafeTestDatabaseError, match="PostgreSQL/RLS") as exc:
        isolated_database_url(tmp_path, environ)
    assert "secret-value" not in str(exc.value)


@pytest.mark.parametrize("as_symlink", [False, True], ids=["existing-file", "symlink"])
def test_existing_database_or_symlink_cannot_be_reused(tmp_path: Path, as_symlink: bool) -> None:
    target = tmp_path / "test.db"
    if as_symlink:
        try:
            target.symlink_to(tmp_path / "missing-target.db")
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                pytest.skip(
                    "NOT RUN: Windows 심볼릭 링크 생성 권한 없음(WinError 1314); "
                    "실제 링크 차단 검증 미실행."
                )
            raise
    else:
        target.write_text("existing data", encoding="utf-8")
    with pytest.raises(UnsafeTestDatabaseError, match="이미 있는 파일"):
        isolated_database_url(tmp_path, {})


def test_fixture_rejects_url_before_creating_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine_calls: list[str] = []

    def forbidden_engine(*args: Any, **kwargs: Any) -> Any:
        engine_calls.append("called")
        raise AssertionError("격리 검증보다 먼저 엔진을 생성했습니다.")

    monkeypatch.setenv("HJ_TEST_DATABASE_URL", "postgresql://app:secret-value@localhost/test_database")
    monkeypatch.setattr(fixtures, "build_session_factory", forbidden_engine)
    fixture_body = fixtures.ctx.__wrapped__(tmp_path)
    with pytest.raises(UnsafeTestDatabaseError):
        next(fixture_body)
    assert engine_calls == []
