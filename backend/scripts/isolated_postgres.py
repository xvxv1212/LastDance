"""검증 스크립트용 새 로컬 PostgreSQL. 기존 URL/DB/시험 가드를 사용하지 않는다.

pgserver 0.1.4 배포에 포함된 바이너리만 사용한다. 매번 새 0700 임시 폴더에
initdb를 실행하고 127.0.0.1 임의 포트만 연다. Unix socket 및 외부 수신은 끈다.
접속 후 실제 PGDATA·주소·포트를 대조한 뒤에만 호출자에게 연결을 제공한다.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from sqlalchemy.engine import URL

DB_ENV_NAMES = (
    "HJ_TEST_DATABASE_URL",
    "HJ_TEST_APP_DATABASE_URL",
    "HJ_DATABASE_URL",
    "HJ_BATCH_DATABASE_URL",
    "HJ_AUTH_DATABASE_URL",
    "HJ_KNOWLEDGE_DATABASE_URL",
    "HJ_CHECKPOINT_DATABASE_URL",
)


class IsolatedPostgresUnavailableError(RuntimeError):
    """새 격리 인스턴스를 만들거나 증명하지 못했다. 외부 URL로 우회하지 않는다."""


def reject_external_database_environment() -> None:
    if any(key in os.environ for key in DB_ENV_NAMES) or any(key.startswith("PG") for key in os.environ):
        raise IsolatedPostgresUnavailableError(
            "BLOCKED_EXTERNAL_DB_ENV: DB 환경변수가 설정되어 검증을 중단합니다."
        )


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError("시험용 DB 식별자가 올바르지 않습니다.")
    return value


@dataclass(frozen=True)
class IsolatedPostgres:
    port: int
    data_directory: Path
    server_version: str
    owner: str = "gwonri_checkpoint"

    def connect(
        self, *, schema: str = "public", user: str | None = None
    ) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            host="127.0.0.1",
            hostaddr="127.0.0.1",
            port=self.port,
            dbname="postgres",
            user=_identifier(user or self.owner),
            options=f"-csearch_path={_identifier(schema)}",
            connect_timeout=3,
            sslmode="disable",
            autocommit=True,
            row_factory=dict_row,
        )

    def url(self, *, schema: str = "public", user: str | None = None) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=_identifier(user or self.owner),
            host="127.0.0.1",
            port=self.port,
            database="postgres",
            query={
                "hostaddr": "127.0.0.1",
                "sslmode": "disable",
                "connect_timeout": "3",
                "options": f"-csearch_path={_identifier(schema)}",
            },
        )


def _run(binary: Path, arguments: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    # 바이너리·인자는 고정 경로/새 임시 폴더에서만 만든다. shell과 외부 URL 입력은 없다.
    return subprocess.run(  # noqa: S603
        [str(binary), *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@contextmanager
def fresh_postgres() -> Iterator[IsolatedPostgres]:
    reject_external_database_environment()
    spec = find_spec("pgserver")
    if spec is None or spec.origin is None:
        raise IsolatedPostgresUnavailableError("BLOCKED_MISSING_PGSERVER: 전용 시험 의존성이 없습니다.")
    binary_dir = Path(spec.origin).parent / "pginstall" / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    initdb, pg_ctl = binary_dir / f"initdb{suffix}", binary_dir / f"pg_ctl{suffix}"
    if not initdb.is_file() or not pg_ctl.is_file():
        raise IsolatedPostgresUnavailableError(
            "BLOCKED_MISSING_BINARIES: PostgreSQL 바이너리를 찾지 못했습니다."
        )

    root = Path(tempfile.mkdtemp(prefix="gwonri-cp-"))
    data = root / "data"
    try:
        initialized = _run(
            initdb,
            [
                "-D",
                str(data),
                "-U",
                "gwonri_checkpoint",
                "--auth-host=trust",
                "--auth-local=reject",
                "--encoding=UTF8",
                "--no-locale",
            ],
        )
        if initialized.returncode:
            raise IsolatedPostgresUnavailableError("BLOCKED_INITDB: 새 DB 초기화를 완료하지 못했습니다.")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        options = f"-h 127.0.0.1 -p {port} -c unix_socket_directories='' -c max_connections=20"
        started = _run(
            pg_ctl,
            [
                "-D",
                str(data),
                "-l",
                str(root / "server.log"),
                "-w",
                "-t",
                "15",
                "-o",
                options,
                "start",
            ],
        )
        if started.returncode:
            raise IsolatedPostgresUnavailableError("BLOCKED_START: 새 로컬 DB 시작을 완료하지 못했습니다.")
        instance = IsolatedPostgres(port, data, "")
        with instance.connect() as conn:
            row = conn.execute(
                "SELECT current_setting('server_version') AS version, "
                "current_setting('data_directory') AS data, "
                "current_setting('listen_addresses') AS listen, "
                "current_setting('unix_socket_directories') AS sockets, "
                "host(inet_server_addr()) AS address, inet_server_port() AS port"
            ).fetchone()
        if (
            row is None
            or Path(row["data"]).resolve() != data.resolve()
            or row["listen"] != "127.0.0.1"
            or row["sockets"] != ""
            or row["address"] != "127.0.0.1"
            or row["port"] != port
        ):
            raise IsolatedPostgresUnavailableError(
                "BLOCKED_IDENTITY: 새 격리 인스턴스임을 증명하지 못했습니다."
            )
        yield IsolatedPostgres(port, data, row["version"])
    finally:
        if (data / "postmaster.pid").exists():
            stopped = _run(pg_ctl, ["-D", str(data), "-w", "-t", "10", "-m", "fast", "stop"], timeout=15)
            if stopped.returncode:
                # 아직 실행 중인 DB 디렉터리를 지우지 않는다. 기존 인스턴스는 건드리지 않는다.
                raise IsolatedPostgresUnavailableError(
                    "BLOCKED_STOP: 생성한 인스턴스 종료를 확인하지 못했습니다."
                )
        shutil.rmtree(root)
