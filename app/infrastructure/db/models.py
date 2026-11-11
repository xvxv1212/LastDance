"""ORM 모델 = 물리 테이블 정의 (테이블명세서의 단일 원본, Single Source of Truth).

- 각 컬럼의 info["ko"] 는 '논리명', comment 는 '설명'으로 테이블명세서에 그대로 출력된다.
- 테이블 순서는 요구사항 분석서의 엔티티 그룹 순서와 같다.
  [사용자] → [AI 점검] → [보고서] → [사건] → [감사] → [파기 대기]
  (업무 DB 11개 테이블. v0.11 에서 purge_tasks 1개 추가 — 시민 자료가 아닌 파기 작업 기록)

v0.6 저장소 분리(2026.11.06 DB 멘토링 피드백 권장안)
- 업무 DB(이 파일)에는 '시민 개인의 업무 기록'만 둔다.
- 공식 출처·청크·임베딩(구 7·8번)은 벡터 지식 DB 로 분리 → app/infrastructure/knowledge.py
- 지원기관 목록(구 17번)은 설정 파일 data/agencies.json
- AI 실행 스레드(구 21번)는 check_sessions·cases 의 thread_id 컬럼,
  실행 기록(구 22번)은 성능·비용은 애플리케이션 로그, 외부 전송·차단은 audit_logs(AI_CALL)
- 대화 원본·요약(구 19·20번)은 LangGraph 체크포인트가 보관하므로 업무 DB 에서 삭제
- 1:1·1:N 이지만 '함께 읽고 함께 바뀌는' 기록(확인 이력·보고서 항목·인용·스냅샷·미확인·상담자료)은
  부모 테이블의 컬럼(JSON 포함)으로 합쳤다. 하나씩 수정·연결되는 타임라인·증거는 테이블로 유지.

무결성 규칙(DB 수정 요청 DB-01~05 반영, v0.6 에서도 유지)
- 소유자 일치: 하위 기록의 소유자 컬럼을 부모의 (id, 소유자) 복합 UNIQUE 에 '복합 FK'로 연결한다.
  → 다른 시민의 점검·사건에 문서·대화·스레드를 연결하면 DB 가 거부한다(DB-03).
- 같은 사건 소속: 증거의 event_id·document_id 는 (id, case_id) 복합 FK 로 '같은 사건'만 허용한다.
- 부모 규칙: 문서는 점검·사건 중 '최소 하나'에 속한다(CHECK).
- 점검↔사건 1:1: 관계는 cases.origin_session_id(UNIQUE) 한 곳에만 둔다(DB-02 관계 단일화).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain import enums
from app.infrastructure.db.base import Base, JsonType, code_enum


def doc(ko: str, desc: str = "") -> dict[str, Any]:
    """컬럼 논리명·설명을 한 번에 지정하는 도우미."""
    return {"info": {"ko": ko}, "comment": desc or ko}


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, **doc("식별자", "UUID v4 기본키(순번 노출 방지)"))


def created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        **doc("생성일시", "행 생성 시각(UTC 저장, 화면은 KST)"),
    )


def updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        **doc("수정일시", "마지막 변경 시각"),
    )


def deleted_at() -> Mapped[datetime | None]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=True,
        **doc("삭제일시", "논리 삭제 시각. 값이 있으면 조회에서 제외, 파기 배치 대상"),
    )


# =============================================================================
# [그룹 1] 사용자·동의
# =============================================================================
class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        # v0.9: 이메일·비밀번호 로그인. 탈퇴 행만 이메일·해시가 비어 있을 수 있다
        CheckConstraint(
            "status = 'WITHDRAWN' OR (email IS NOT NULL AND password_hash IS NOT NULL)",
            name="login_required",
        ),
        CheckConstraint(
            "status <> 'WITHDRAWN' OR (email IS NULL AND password_hash IS NULL"
            " AND display_name IS NULL AND deleted_at IS NOT NULL)",
            name="withdrawn_has_no_identity",
        ),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        {"comment": "서비스 회원 계정(탈퇴 시 식별정보를 지운 빈 행으로 남김)", "info": {"ko": "사용자"}},
    )

    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str | None] = mapped_column(
        String(254),
        unique=True,
        **doc("이메일", "로그인 ID. 소문자로 정규화하여 저장. v0.9: 탈퇴 시 NULL(같은 이메일 재가입 허용)"),
    )
    password_hash: Mapped[str | None] = mapped_column(
        String(255), **doc("비밀번호 해시", "Argon2id 해시(평문 저장 금지). v0.9: 탈퇴 시 NULL")
    )
    display_name: Mapped[str | None] = mapped_column(
        String(50), **doc("표시 이름", "실명 아님. 선택 입력. 탈퇴 시 NULL")
    )
    role: Mapped[enums.UserRole] = mapped_column(
        code_enum(enums.UserRole, "role"),
        nullable=False,
        server_default=enums.UserRole.CITIZEN.value,
        **doc("역할", "CITIZEN/COUNSELOR/ADMIN. v0.9 MVP 는 CITIZEN 만 사용(상담사·관리자 원문 열람 없음)"),
    )
    status: Mapped[enums.UserStatus] = mapped_column(
        code_enum(enums.UserStatus, "status"),
        nullable=False,
        server_default=enums.UserStatus.ACTIVE.value,
        **doc("계정 상태", "ACTIVE/DORMANT/WITHDRAWN. ACTIVE 가 아니면 모든 API 거부"),
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        **doc("로그인 실패 횟수", "연속 실패 횟수(성공 시 0). 기준 횟수에 이르면 잠금"),
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), **doc("잠금 해제 시각", "연속 실패로 잠긴 경우 이 시각까지 로그인 거부")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        **doc("탈퇴일시", "탈퇴 시각. 감사 로그 보관기간이 지나면 이 빈 행도 파기"),
    )


class UserConsentModel(Base):
    __tablename__ = "user_consents"
    __table_args__ = (
        Index(None, "user_id", "consent_type", "agreed_at"),
        {
            "comment": "개인정보·문서·국외이전 동의 및 철회 이력(append-only)",
            "info": {"ko": "사용자 동의 이력"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, **doc("사용자 ID", "users.id")
    )
    consent_type: Mapped[enums.ConsentType] = mapped_column(
        code_enum(enums.ConsentType, "consent_type"),
        nullable=False,
        **doc("동의 종류", "PRIVACY_REQUIRED/SENSITIVE_DOCUMENT/OVERSEAS_TRANSFER"),
    )
    policy_version: Mapped[str] = mapped_column(
        String(20), nullable=False, **doc("약관 버전", "동의 당시 고지문 버전(예: 2026-11-v1)")
    )
    agreed: Mapped[bool] = mapped_column(Boolean, nullable=False, **doc("동의 여부", "true=동의, false=철회"))
    agreed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, **doc("처리일시", "동의 또는 철회 처리 시각")
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), **doc("철회일시", "철회 시에만 기록")
    )


# =============================================================================
# [그룹 2] AI 점검 (세션·문서·추출 사실·확인 이력)
# =============================================================================
class CheckSessionModel(Base):
    __tablename__ = "check_sessions"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),  # 하위 테이블 '소유자 일치' 복합 FK 의 대상
        Index(None, "user_id", "created_at"),
        Index(None, "updated_at"),  # v0.9: 미전환 점검 파기(마지막 이용 후 30일) 조회용
        {
            "comment": "AI 통합 점검 1회분(상황 입력~보고서). 사건 연결은 cases.origin_session_id 로만 관리",
            "info": {"ko": "AI 점검 세션"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, **doc("사용자 ID", "점검 소유자, users.id")
    )
    entry_type: Mapped[enums.EntryType] = mapped_column(
        code_enum(enums.EntryType, "entry_type"),
        nullable=False,
        **doc("진입 경로", "REFUND_CONCERN/DISPUTE_OCCURRED/PRE_CONTRACT"),
    )
    situation_text: Mapped[str | None] = mapped_column(
        Text,
        **doc(
            "상황 설명",
            "생활언어 입력 원문(고유식별정보 마스킹 후 저장). v0.8: PDF 만으로 시작하면 NULL"
            "(이때는 계약서 PDF 가 있어야 사실 추출 가능)",
        ),
    )
    status: Mapped[enums.SessionStatus] = mapped_column(
        code_enum(enums.SessionStatus, "status"),
        nullable=False,
        server_default=enums.SessionStatus.DRAFT.value,
        **doc("진행 상태", "DRAFT→FACTS_EXTRACTED→FACTS_CONFIRMED→REPORTED→CONVERTED / ABANDONED"),
    )
    facts_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        **doc("사실 승인일시", "사용자가 추출 사실 전체를 승인한 시각(DEC-01: 사건 전 승인 기록)"),
    )
    thread_id: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        **doc(
            "AI 스레드 ID", "LangGraph thread_id(추측 불가 랜덤). 구 agent_threads 대체, 체크포인트 파기 기준"
        ),
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = deleted_at()


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="size_range"),
        CheckConstraint("page_count IS NULL OR page_count >= 1", name="page_count_positive"),
        CheckConstraint("check_session_id IS NOT NULL OR case_id IS NOT NULL", name="parent_required"),
        # 소유자 일치: 문서 소유자 = 점검 소유자 = 사건 소유자
        ForeignKeyConstraint(
            ["check_session_id", "owner_id"],
            ["check_sessions.id", "check_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_documents_session_owner",
        ),
        ForeignKeyConstraint(
            ["case_id", "owner_id"],
            ["cases.id", "cases.owner_id"],
            ondelete="CASCADE",
            name="fk_documents_case_owner",
        ),
        UniqueConstraint("id", "check_session_id"),  # 추출 사실의 '같은 점검 문서' 복합 FK 대상
        UniqueConstraint("id", "case_id"),  # 증거의 '같은 사건 문서' 복합 FK 대상
        Index(None, "check_session_id"),
        Index(None, "case_id"),
        Index(None, "retention_until"),
        {
            "comment": "업로드 파일 메타데이터(본문은 암호화 객체 저장소). 점검·사건 중 최소 하나에 소속",
            "info": {"ko": "업로드 문서"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        **doc("소유자 ID", "users.id, 부모 점검·사건 소유자와 같아야 함"),
    )
    check_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, **doc("점검 세션 ID", "업로드된 점검"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, **doc("사건 ID", "사건 전환·증거 첨부 시 연결"))
    original_name: Mapped[str] = mapped_column(
        String(200), nullable=False, **doc("원본 파일명", "표시용(경로 문자 제거). 저장 경로로 사용 금지")
    )
    mime_type: Mapped[str] = mapped_column(
        String(100), nullable=False, **doc("MIME 유형", "MVP 는 application/pdf 만 허용")
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, **doc("파일 크기", "바이트, 최대 10MB")
    )
    storage_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, **doc("저장소 키", "추측 불가 랜덤 키(객체 저장소 경로)")
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, **doc("SHA-256", "파일 무결성 해시"))
    page_count: Mapped[int | None] = mapped_column(Integer, **doc("페이지 수", "PDF 파싱 후 기록"))
    retention_until: Mapped[date] = mapped_column(
        Date, nullable=False, **doc("보관 기한", "정보용. v0.8: 문서 단독 파기 안 함, 사건·점검과 함께 파기")
    )
    created_at: Mapped[datetime] = created_at()
    deleted_at: Mapped[datetime | None] = deleted_at()


class ExtractedFactModel(Base):
    __tablename__ = "extracted_facts"
    __table_args__ = (
        CheckConstraint(
            "source_kind <> 'DOCUMENT' OR document_id IS NOT NULL", name="document_source_required"
        ),
        CheckConstraint("(source_kind = 'UNKNOWN') = (value_text IS NULL)", name="unknown_has_no_value"),
        CheckConstraint("page_no IS NULL OR page_no >= 1", name="page_no_positive"),
        CheckConstraint("(offset_start IS NULL) = (offset_end IS NULL)", name="offset_pair"),
        CheckConstraint(
            "offset_start IS NULL OR (offset_start >= 0 AND offset_end >= offset_start)", name="offset_range"
        ),
        # 확인자·확인일시는 '확인 상태'와 함께 채워진다(구 fact_confirmations 대체).
        # v0.8: confirm_action 컬럼 삭제(멘토 10번) — 처리 종류는 status 로 1:1 대응한다
        #   CONFIRM ↔ CONFIRMED, MODIFY ↔ MODIFIED, REJECT ↔ REJECTED, 미처리 ↔ EXTRACTED
        #   v0.11: ACKNOWLEDGE_UNKNOWN ↔ ACKNOWLEDGED_UNKNOWN (확인자·일시 = 모름을 인지 처리한 사람·시각)
        CheckConstraint(
            "(status = 'EXTRACTED') = (confirmed_at IS NULL)"
            " AND (confirmed_at IS NULL) = (confirmed_by IS NULL)",
            name="confirmation_complete",
        ),
        # v0.11: '모름 인지' 상태는 미확인(UNKNOWN) 항목에만 쓸 수 있다(값이 있는 사실에 붙일 수 없음)
        CheckConstraint(
            "status <> 'ACKNOWLEDGED_UNKNOWN' OR source_kind = 'UNKNOWN'", name="ack_only_unknown"
        ),
        # 출처 문서는 '같은 점검'에 올린 문서여야 한다(다른 사건 자료 혼입 금지)
        ForeignKeyConstraint(
            ["document_id", "check_session_id"],
            ["documents.id", "documents.check_session_id"],
            ondelete="CASCADE",
            name="fk_extracted_facts_document_same_session",
        ),
        Index(None, "check_session_id", "fact_key"),
        {
            "comment": "1차 추론이 추출한 사실·근거 위치와 사용자 확인 결과"
            "(ai_value_text 에 AI 원래 값 보존)",
            "info": {"ko": "추출 사실"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    check_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_sessions.id", ondelete="CASCADE"),
        nullable=False,
        **doc("점검 세션 ID", "check_sessions.id"),
    )
    fact_key: Mapped[enums.FactKey] = mapped_column(
        code_enum(enums.FactKey, "fact_key"),
        nullable=False,
        **doc("사실 항목", "필수 8항목 등(CONTRACT_DATE, PAYMENT_METHOD …)"),
    )
    value_text: Mapped[str | None] = mapped_column(
        Text, **doc("값(원문)", "사용자에게 보이는 값. UNKNOWN 이면 NULL")
    )
    value_normalized: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType, **doc("정규화 값", '{"date":"2026-03-02"}, {"amount":720000,"currency":"KRW"} 등')
    )
    source_kind: Mapped[enums.FactSourceKind] = mapped_column(
        code_enum(enums.FactSourceKind, "source_kind"),
        nullable=False,
        **doc("근거 유형", "DOCUMENT(원문)/STATEMENT(진술)/UNKNOWN(미확인)"),
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, **doc("출처 문서 ID", "근거가 문서일 때 필수")
    )
    page_no: Mapped[int | None] = mapped_column(Integer, **doc("페이지", "출처 페이지 번호(1부터)"))
    offset_start: Mapped[int | None] = mapped_column(Integer, **doc("시작 오프셋", "원문 문자 위치(0부터)"))
    offset_end: Mapped[int | None] = mapped_column(Integer, **doc("끝 오프셋", "원문 문자 위치(시작 이상)"))
    source_span: Mapped[str | None] = mapped_column(Text, **doc("출처 구절", "근거가 된 원문 일부"))
    status: Mapped[enums.FactStatus] = mapped_column(
        code_enum(enums.FactStatus, "status"),
        nullable=False,
        server_default=enums.FactStatus.EXTRACTED.value,
        **doc(
            "확인 상태",
            "EXTRACTED/CONFIRMED/MODIFIED/REJECTED/ACKNOWLEDGED_UNKNOWN "
            "(CONFIRMED·MODIFIED = 승인된 사실, ACKNOWLEDGED_UNKNOWN = 모름 인지·값 없음)",
        ),
    )
    has_conflict: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        **doc("충돌 여부", "같은 항목에 다른 값이 있으면 true"),
    )
    ai_value_text: Mapped[str | None] = mapped_column(
        Text, **doc("AI 추출값", "AI 가 처음 추출한 값(수정 후에도 변경하지 않음, AI-01). UNKNOWN 이면 NULL")
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), **doc("확인자 ID", "users.id, 마지막으로 확인·수정·거부한 사용자")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), **doc("확인일시", "마지막 확인 처리 시각. 처리 이력은 audit_logs(UPDATE)")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


# =============================================================================
# [그룹 3] 점검 보고서
# =============================================================================
class InspectionReportModel(Base):
    __tablename__ = "inspection_reports"
    __table_args__ = (
        UniqueConstraint("check_session_id", "version"),
        UniqueConstraint("id", "check_session_id"),  # v0.8 G1: 사건의 근거 보고서 복합 FK 대상
        CheckConstraint("version >= 1", name="version_positive"),
        {
            "comment": "3분류 점검 보고서(버전 관리, 승인 후 본문 불변). 항목·인용·검증 결과는 items JSON",
            "info": {"ko": "점검 보고서"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    check_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("check_sessions.id", ondelete="CASCADE"),
        nullable=False,
        **doc("점검 세션 ID", "check_sessions.id"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, **doc("버전", "세션별 1부터 증가"))
    status: Mapped[enums.ReportStatus] = mapped_column(
        code_enum(enums.ReportStatus, "status"),
        nullable=False,
        server_default=enums.ReportStatus.DRAFT.value,
        **doc("상태", "DRAFT/APPROVED/SUPERSEDED"),
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, **doc("요약", "보고서 머리 요약"))
    items: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType,
        nullable=False,
        **doc(
            "보고서 항목",
            "JSON 배열(구 report_items·report_citations): 분류·내용·연결 사실·보류 사유·인용"
            "(청크 ID·인용문·검증 결과·인용 당시 출처 제목/URL/시행일)",
        ),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), **doc("승인일시", "사용자 최종 확인 시각")
    )
    created_at: Mapped[datetime] = created_at()


# =============================================================================
# [그룹 4] 사건
# =============================================================================
# v0.8 G2: 승인·대체된 보고서는 본문을 바꿀 수 없다(정정은 '새 판'을 승인하는 방식으로만).
# 앱에는 보고서 수정 경로가 없지만, 직접 SQL·향후 코드 실수까지 DB 가 막도록 PostgreSQL 트리거로 둔다.
# 상태는 APPROVED → SUPERSEDED 만 허용, 파기(DELETE)는 막지 않는다. SQLite(테스트)에서는 만들지 않는다.
REPORT_IMMUTABLE_DDL = """
CREATE OR REPLACE FUNCTION forbid_approved_report_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('APPROVED', 'SUPERSEDED') AND (
           NEW.summary_text IS DISTINCT FROM OLD.summary_text
        OR NEW.items::text IS DISTINCT FROM OLD.items::text
        OR NEW.version IS DISTINCT FROM OLD.version
        OR NEW.check_session_id IS DISTINCT FROM OLD.check_session_id
        OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
        OR NOT (NEW.status = OLD.status OR (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED'))
    ) THEN
        RAISE EXCEPTION 'approved report % is immutable', OLD.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_inspection_reports_immutable ON inspection_reports;
CREATE TRIGGER trg_inspection_reports_immutable
    BEFORE UPDATE ON inspection_reports
    FOR EACH ROW EXECUTE FUNCTION forbid_approved_report_change();
""".strip()

event.listen(
    InspectionReportModel.__table__,
    "after_create",
    # DDL 은 % 를 치환 문자로 쓰므로 RAISE 메시지의 % 를 %% 로 바꿔 넘긴다
    DDL(REPORT_IMMUTABLE_DDL.replace("%", "%%")).execute_if(  # type: ignore[no-untyped-call]  # SQLAlchemy 타입 미제공
        dialect="postgresql"
    ),
)


class CaseModel(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("id", "owner_id"),  # 하위 테이블 '소유자 일치' 복합 FK 의 대상
        # 원본 점검은 같은 사용자 소유여야 한다(타인 점검을 내 사건으로 전환 불가)
        ForeignKeyConstraint(
            ["origin_session_id", "owner_id"],
            ["check_sessions.id", "check_sessions.user_id"],
            name="fk_cases_origin_session_owner",
        ),
        Index(None, "owner_id", "status"),
        # 확인 사실 스냅샷은 사실 묶음·버전·승인일시가 함께 있어야 한다(DEC-01)
        CheckConstraint(
            "(verified_facts IS NULL) = (snapshot_version IS NULL)"
            " AND (verified_facts IS NULL) = (snapshot_approved_at IS NULL)",
            name="snapshot_complete",
        ),
        CheckConstraint(
            "snapshot_version IS NULL OR snapshot_version >= 1", name="snapshot_version_positive"
        ),
        # v0.8 G1: 근거 보고서는 '원본 점검의 보고서'만 가리킨다(다른 점검·타인 보고서 연결 차단).
        # 복합 FK 는 한쪽이 NULL 이면 검사하지 않으므로, 근거 보고서가 있으면 원본 점검도 필수로 둔다.
        ForeignKeyConstraint(
            ["snapshot_report_id", "origin_session_id"],
            ["inspection_reports.id", "inspection_reports.check_session_id"],
            name="fk_cases_snapshot_report_same_session",
        ),
        CheckConstraint(
            "snapshot_report_id IS NULL OR origin_session_id IS NOT NULL",
            name="snapshot_report_needs_origin",
        ),
        {
            "comment": "시민의 분쟁 사건. 점검 전환이면 origin_session_id(UNIQUE), 신규 직행이면 NULL."
            " 확인 사실 스냅샷·미확인 정보·상담 준비자료는 컬럼(JSON)으로 보관",
            "info": {"ko": "사건"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, **doc("소유자 ID", "users.id")
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False, **doc("사건 제목", "2~100자"))
    case_type: Mapped[enums.CaseType] = mapped_column(
        code_enum(enums.CaseType, "case_type"),
        nullable=False,
        **doc("사건 유형", "FITNESS_REFUND/OTHER_CONSUMER"),
    )
    status: Mapped[enums.CaseStatus] = mapped_column(
        code_enum(enums.CaseStatus, "status"),
        nullable=False,
        server_default=enums.CaseStatus.OPEN.value,
        **doc("상태", "OPEN/PREPARING/CONSULTING/CLOSED"),
    )
    origin_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        unique=True,
        **doc("원본 점검 ID", "전환 출처(UNIQUE: 점검 1건 → 사건 최대 1건), 신규 사건은 NULL"),
    )
    business_name: Mapped[str | None] = mapped_column(String(100), **doc("상대 사업자명"))
    thread_id: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        **doc("AI 스레드 ID", "사건에서 시작한 LangGraph thread_id(신규 직행 포함). 구 agent_threads 대체"),
    )
    verified_facts: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType,
        **doc(
            "확인 사실", "사건의 정본 사실 묶음 JSON(키·값·근거 유형·fact_id). 구 verified_snapshots.payload"
        ),
    )
    snapshot_version: Mapped[int | None] = mapped_column(
        Integer, **doc("스냅샷 버전", "확인 사실 버전(전환 시 1). 버전 변경 이력은 audit_logs")
    )
    snapshot_report_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        **doc(
            "근거 보고서 ID",
            "스냅샷을 만든 승인 보고서(원본 점검의 보고서만, v0.8 복합 FK). 신규 사건 직행이면 NULL",
        ),
    )
    snapshot_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), **doc("스냅샷 승인일시", "사용자가 사실을 승인한 시각")
    )
    unverified_items: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
        **doc("미확인 정보", "JSON 배열[{id, content, status, resolved_at}]. 구 unverified_items 테이블"),
    )
    consultation_prep: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType,
        **doc(
            "상담 준비자료",
            "최신본 JSON{version, status, agency_code, summary_text, checklist, questions, created_at}."
            " 구 consultation_preps",
        ),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), **doc("종결일시"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = deleted_at()


class CaseEventModel(Base):
    __tablename__ = "case_events"
    __table_args__ = (
        UniqueConstraint("id", "case_id"),  # 증거의 '같은 사건 타임라인' 복합 FK 대상
        Index(None, "case_id", "occurred_on"),
        {"comment": "사건 타임라인(언제·누가·무엇을)", "info": {"ko": "사건 타임라인"}},
    )

    id: Mapped[uuid.UUID] = pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, **doc("사건 ID", "cases.id")
    )
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False, **doc("발생일", "미래 날짜 불가"))
    actor: Mapped[enums.EventActor] = mapped_column(
        code_enum(enums.EventActor, "actor"), nullable=False, **doc("행위 주체", "USER/BUSINESS/AGENCY/OTHER")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, **doc("내용 제목", "예: 해지·환불 요청"))
    description: Mapped[str | None] = mapped_column(Text, **doc("상세 내용"))
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), **doc("사용자 확인 여부")
    )
    created_at: Mapped[datetime] = created_at()


class EvidenceModel(Base):
    __tablename__ = "evidences"
    __table_args__ = (
        # 연결 타임라인·첨부 문서는 '같은 사건' 것만 허용
        ForeignKeyConstraint(
            ["event_id", "case_id"],
            ["case_events.id", "case_events.case_id"],
            name="fk_evidences_event_same_case",
        ),
        ForeignKeyConstraint(
            ["document_id", "case_id"],
            ["documents.id", "documents.case_id"],
            name="fk_evidences_document_same_case",
        ),
        Index(None, "case_id", "status"),
        {
            "comment": "사건 증거 목록(보유·미보유·요청). 타임라인·문서는 같은 사건 것만 연결",
            "info": {"ko": "증거"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, **doc("사건 ID", "cases.id")
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, **doc("관련 타임라인 ID", "같은 사건의 case_events.id")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, **doc("첨부 문서 ID", "같은 사건의 documents.id")
    )
    evidence_type: Mapped[enums.EvidenceType] = mapped_column(
        code_enum(enums.EvidenceType, "evidence_type"),
        nullable=False,
        **doc("증거 유형", "CONTRACT/PAYMENT_RECEIPT/MESSAGE/CALL_RECORD/PHOTO/NOTICE/OTHER"),
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, **doc("증거명"))
    description: Mapped[str | None] = mapped_column(Text, **doc("설명"))
    obtained_on: Mapped[date | None] = mapped_column(Date, **doc("확보일"))
    status: Mapped[enums.EvidenceStatus] = mapped_column(
        code_enum(enums.EvidenceStatus, "status"),
        nullable=False,
        **doc("보유 상태", "HELD/MISSING/REQUESTED"),
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


# =============================================================================
# [그룹 5] 감사 로그
# =============================================================================
class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(None, "actor_id", "created_at"),
        Index(None, "created_at"),  # v0.9: 보관기간(잠정 90일) 경과분 파기 조회용
        Index(None, "target_type", "target_id"),
        {
            "comment": "민감 자원 생성·열람·수정·삭제·동의·파기·외부 AI 호출 감사 로그"
            "(수정·삭제 불가, 원문 미기록)",
            "info": {"ko": "감사 로그"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), **doc("행위자 ID", "시스템 작업은 NULL")
    )
    action: Mapped[enums.AuditAction] = mapped_column(
        code_enum(enums.AuditAction, "action"),
        nullable=False,
        **doc("행위", "CREATE/READ_SENSITIVE/UPDATE/DELETE/EXPORT/CONSENT/PURGE/AI_CALL"),
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, **doc("대상 테이블"))
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, **doc("대상 ID", "파기 후에도 남는 식별자(개인정보 아님)")
    )
    ip_hash: Mapped[str | None] = mapped_column(String(64), **doc("IP 해시", "원본 IP 대신 솔트 해시"))
    detail: Mapped[dict[str, Any] | None] = mapped_column(
        JsonType,
        **doc("상세", "건수·코드만(AI_CALL: 노드·상태·외부 전송 여부·처리 근거·차단 코드). 원문 기록 금지"),
    )
    created_at: Mapped[datetime] = created_at()


# =============================================================================
# [그룹 6] 파기 대기 작업 (v0.11)
# =============================================================================
class PurgeTaskModel(Base):
    __tablename__ = "purge_tasks"
    __table_args__ = (
        UniqueConstraint("kind", "target"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index(None, "kind", "created_at"),
        {
            "comment": "DB 밖 자료(파일·체크포인트) 삭제 대기 기록. DB 행을 지우는 트랜잭션에서 함께 쓰고, "
            "삭제가 끝나면 행을 지운다(행이 남아 있다 = 아직 지우지 못했다). 시민 접속 롤은 접근 불가",
            "info": {"ko": "파기 대기 작업"},
        },
    )

    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[enums.PurgeTaskKind] = mapped_column(
        code_enum(enums.PurgeTaskKind, "kind"),
        nullable=False,
        **doc("종류", "FILE(저장소 파일)/CHECKPOINT(LangGraph 체크포인트)"),
    )
    target: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        **doc("대상", "FILE=storage_key(랜덤 키), CHECKPOINT=thread_id(랜덤). 이름·내용 등 개인정보 아님"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), **doc("시도 횟수", "삭제를 시도한 횟수")
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(40),
        **doc("마지막 실패 코드", "DELETE_FAILED/STORE_NOT_CONNECTED. 오류 원문·경로는 기록하지 않는다"),
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), **doc("마지막 시도일시", "아직 시도 전이면 NULL")
    )
    created_at: Mapped[datetime] = created_at()
