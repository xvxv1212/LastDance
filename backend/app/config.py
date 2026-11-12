"""환경설정. 비밀값은 코드에 두지 않고 환경변수로만 받는다(하드코딩 금지)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.legacy_settings import get_settings as get_settings


def _flag(value: str | None) -> bool:
    """켜짐으로 인정하는 값만 True. 비어 있거나 모르는 값은 False(닫힘이 기본)."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    storage_dir: Path
    document_retention_days: int = 180
    cors_origins: tuple[str, ...] = ()
    # v0.6: 업무 DB 밖의 저장소(벡터 지식 DB 적재 원본, 지원기관 목록)
    knowledge_path: Path = Path("./data/knowledge/sources.json")
    # 본문·출처 URL·버전·원문 대조·사용자 승인 증거가 연결된 서버 관리 파일.
    # 파일이 없으면 저장된 자료가 있더라도 새 보고서의 인용은 허용하지 않는다.
    knowledge_approval_path: Path = Path("./data/knowledge/approvals.json")
    # v0.9 결정 6(A안): 값이 있으면 pgvector 지식 DB(knowledge 스키마)를 읽는다. 비우면 JSON 파일(개발용)
    knowledge_database_url: str | None = None
    agencies_path: Path = Path("./data/agencies.json")
    # v0.8 RLS: 파기 배치 전용 접속(BYPASSRLS 롤). 비우면 앱 접속을 그대로 쓴다(개발용)
    batch_database_url: str | None = None
    # v0.9 가입·로그인 전용 접속(users·audit_logs 만, RLS 우회).
    # 비우면 파기 배치 접속 → 앱 접속 순으로 쓴다(개발용)
    auth_database_url: str | None = None
    # v0.9 AI 내부 API 서비스 토큰 비밀키(시민 토큰과 다른 값). 비우면 AI 내부 API 전부 거부
    service_jwt_secret: str | None = None
    access_token_minutes: int = 60
    # v0.9 로그인 연속 실패 잠금
    login_max_failures: int = 5
    login_lock_minutes: int = 10
    # v0.9 데이터별 보관 기간(잠정, 법정 기간 아님)
    deleted_case_grace_days: int = 30
    unconverted_session_days: int = 30
    closed_case_days: int = 30
    audit_log_days: int = 90
    # v0.11 SUP-01: 공개 회원가입 스위치. 기본은 닫힘(False) — 명시적으로 켜야만 /auth/signup 이 열린다.
    # 닫혀 있을 때 시험 계정은 scripts/issue_test_account.py(인증 전용 DB 접속 권한 필요)로만 만든다.
    public_signup_enabled: bool = False
    # v0.11 SUP-03: 체크포인트 저장소 연결. 셋 중 하나다.
    #   checkpoint_database_url 있음 = LangGraph 체크포인트 DB 에 연결해 thread 단위로 지운다
    #   checkpoint_store="NOT_USED"  = 이 배포는 체크포인트 저장소를 쓰지 않는다고 '명시'(지울 자료 없음)
    #   둘 다 없음                   = 연결 안 됨/자료 유무 불명 → 체크포인트 파기는 '미완료(대기)'로 남긴다
    checkpoint_database_url: str | None = None
    checkpoint_store: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("HJ_JWT_SECRET")
        if not secret:
            raise RuntimeError("환경변수 HJ_JWT_SECRET 이 설정되지 않았습니다.")
        return cls(
            database_url=os.environ.get("HJ_DATABASE_URL", "postgresql+psycopg://localhost/gwonri_chaebi"),
            jwt_secret=secret,
            storage_dir=Path(os.environ.get("HJ_STORAGE_DIR", "./var/files")),
            document_retention_days=int(os.environ.get("HJ_RETENTION_DAYS", "180")),
            cors_origins=tuple(o for o in os.environ.get("HJ_CORS_ORIGINS", "").split(",") if o),
            knowledge_path=Path(os.environ.get("HJ_KNOWLEDGE_PATH", "./data/knowledge/sources.json")),
            knowledge_approval_path=Path(
                os.environ.get("HJ_KNOWLEDGE_APPROVAL_PATH", "./data/knowledge/approvals.json")
            ),
            knowledge_database_url=os.environ.get("HJ_KNOWLEDGE_DATABASE_URL") or None,
            agencies_path=Path(os.environ.get("HJ_AGENCIES_PATH", "./data/agencies.json")),
            batch_database_url=os.environ.get("HJ_BATCH_DATABASE_URL") or None,
            auth_database_url=os.environ.get("HJ_AUTH_DATABASE_URL") or None,
            service_jwt_secret=os.environ.get("HJ_SERVICE_JWT_SECRET") or None,
            access_token_minutes=int(os.environ.get("HJ_ACCESS_TOKEN_MINUTES", "60")),
            login_max_failures=int(os.environ.get("HJ_LOGIN_MAX_FAILURES", "5")),
            login_lock_minutes=int(os.environ.get("HJ_LOGIN_LOCK_MINUTES", "10")),
            deleted_case_grace_days=int(os.environ.get("HJ_DELETED_CASE_GRACE_DAYS", "30")),
            unconverted_session_days=int(os.environ.get("HJ_UNCONVERTED_SESSION_DAYS", "30")),
            closed_case_days=int(os.environ.get("HJ_CLOSED_CASE_DAYS", "30")),
            audit_log_days=int(os.environ.get("HJ_AUDIT_LOG_DAYS", "90")),
            public_signup_enabled=_flag(os.environ.get("HJ_PUBLIC_SIGNUP_ENABLED")),
            checkpoint_database_url=os.environ.get("HJ_CHECKPOINT_DATABASE_URL") or None,
            checkpoint_store=(os.environ.get("HJ_CHECKPOINT_STORE") or "").strip().upper() or None,
        )

    def __post_init__(self) -> None:
        if self.checkpoint_store not in (None, "NOT_USED"):
            # 모르는 값을 '연결됨'이나 '미사용'으로 짐작하지 않는다
            raise RuntimeError("HJ_CHECKPOINT_STORE 는 비우거나 NOT_USED 만 쓸 수 있습니다.")
        if self.checkpoint_store == "NOT_USED" and self.checkpoint_database_url:
            raise RuntimeError(
                "HJ_CHECKPOINT_STORE=NOT_USED 와 HJ_CHECKPOINT_DATABASE_URL 을 함께 쓸 수 없습니다."
            )
        if self.service_jwt_secret is not None and self.service_jwt_secret == self.jwt_secret:
            raise RuntimeError("HJ_SERVICE_JWT_SECRET 은 HJ_JWT_SECRET 과 다른 값이어야 합니다.")
