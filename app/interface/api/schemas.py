"""요청·응답 스키마(Pydantic).

- extra="forbid": 정의되지 않은 필드가 오면 거부 → 대량 할당(Mass Assignment) 방지
- 문자열 길이 상한을 두어 과도한 입력으로 인한 자원 고갈을 막는다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain import enums


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IdOut(BaseModel):
    id: uuid.UUID


# --- 동의·점검 --------------------------------------------------------------
class ConsentIn(_In):
    consent_type: enums.ConsentType
    policy_version: str = Field(min_length=1, max_length=20)
    agreed: bool


class StartSessionIn(_In):
    entry_type: enums.EntryType
    # v0.8: 생략 가능(PDF 만으로 시작). 보내면 5~4,000자
    situation_text: str | None = Field(default=None, min_length=5, max_length=4000)


class FactIn(_In):
    fact_key: enums.FactKey
    value_text: str | None = Field(default=None, min_length=1, max_length=2000)  # UNKNOWN 이면 생략
    source_kind: enums.FactSourceKind
    value_normalized: dict[str, Any] | None = None
    document_id: uuid.UUID | None = None
    page_no: int | None = Field(default=None, ge=1)
    offset_start: int | None = Field(default=None, ge=0)
    offset_end: int | None = Field(default=None, ge=0)
    source_span: str | None = Field(default=None, max_length=2000)
    # v0.10(2026-11-11 결정): confidence 는 받지 않는다. 보내면 extra=forbid 로 422


class FactsIn(_In):
    facts: list[FactIn] = Field(min_length=1, max_length=100)


class FactIdsOut(BaseModel):
    ids: list[uuid.UUID]


class ConfirmFactIn(_In):
    action: enums.ConfirmAction
    new_value: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _no_value_for_unknown_ack(self) -> ConfirmFactIn:
        # v0.11: '모름' 처리에는 값을 보내지 않는다. 빈 문자열·공백(잘린 뒤 "")·추정값 모두 422
        if self.action == enums.ConfirmAction.ACKNOWLEDGE_UNKNOWN and self.new_value is not None:
            raise ValueError("ACKNOWLEDGE_UNKNOWN 에는 new_value 를 보내지 않습니다(생략 또는 null).")
        return self


class SessionStatusOut(BaseModel):
    session_status: enums.SessionStatus


class FactOut(BaseModel):
    """사실 1건(v0.11 조회). 파일 경로·저장소 키·confidence 는 내보내지 않는다."""

    id: uuid.UUID
    fact_key: enums.FactKey
    ai_value: str | None  # AI 가 처음 추출한 값(사용자가 고쳐도 바뀌지 않음). 미확인이면 null
    current_value: str | None  # 사용자 확인·수정이 반영된 현재 값. 미확인·모름이면 null
    value_normalized: dict[str, Any] | None
    status: enums.FactStatus  # ACKNOWLEDGED_UNKNOWN = 모름 인지(사실 확인 아님)
    # 처리 시각: CONFIRMED·MODIFIED·REJECTED 는 그 처리 시각, ACKNOWLEDGED_UNKNOWN 은 모름 인지 시각
    confirmed_at: datetime | None
    has_conflict: bool
    source_kind: enums.FactSourceKind
    document_id: uuid.UUID | None  # 출처 문서 ID(경로 아님)
    page_no: int | None
    offset_start: int | None
    offset_end: int | None
    source_span: str | None


class SessionFactsOut(BaseModel):
    check_session_id: uuid.UUID
    session_status: enums.SessionStatus
    facts_confirmed_at: datetime | None  # 보고서 초안 제출 때 facts_confirmed_at 로 되돌려 보낼 수 있는 값
    facts: list[FactOut]


# --- 보고서 -----------------------------------------------------------------
class CitationIn(_In):
    """'검증됨' 값은 받지 않는다(extra=forbid 로 거부) — 서버가 판정한다(AI-03)."""

    # 벡터 지식 DB 의 청크 ID(v0.6: 업무 DB 와 분리되어 UUID 가 아닌 문자열 ID)
    source_chunk_id: str = Field(min_length=1, max_length=64)
    quoted_text: str = Field(min_length=1, max_length=2000)


class ReportItemIn(_In):
    category: enums.ReportCategory
    content: str = Field(min_length=1, max_length=2000)
    fact_id: uuid.UUID | None = None
    citations: list[CitationIn] = Field(default_factory=list, max_length=10)


class ReportIn(_In):
    summary_text: str = Field(min_length=1, max_length=4000)
    items: list[ReportItemIn] = Field(min_length=1, max_length=100)
    # v0.11(선택): 초안을 만들 때 기준으로 삼은 사실 승인 시각. 보내면 현재 값과 다를 때 409
    facts_confirmed_at: datetime | None = None


class ReportOut(BaseModel):
    id: uuid.UUID
    held_items: int


class CitationOut(BaseModel):
    """인용 '당시'의 출처 사본과 서버 검증 결과. 지식 DB 가 바뀌어도 이 값은 그대로다."""

    requested_chunk_ref: str
    chunk_id: str | None
    quoted_text: str
    check_result: enums.CitationCheck
    source_title: str | None
    source_url: str | None
    article_ref: str | None
    effective_date: date | None


class ReportItemOut(BaseModel):
    id: uuid.UUID
    category: enums.ReportCategory
    content: str
    fact_id: uuid.UUID | None
    hold_reason: enums.HoldReason | None  # 값이 있으면 보류 항목(확정 내용으로 보여 주지 않는다)
    citations: list[CitationOut]


class ReportDetailOut(BaseModel):
    id: uuid.UUID
    check_session_id: uuid.UUID
    version: int
    status: enums.ReportStatus
    summary_text: str
    created_at: datetime
    approved_at: datetime | None
    is_stale: bool  # true = 초안 작성 뒤 사실이 바뀜 → 승인 불가(새 초안 필요)
    held_items: int
    items: list[ReportItemOut]


# --- 사건 -------------------------------------------------------------------
class ConvertOut(BaseModel):
    id: uuid.UUID
    created: bool  # false = 이미 전환된 사건을 그대로 돌려준 멱등 응답


class ThreadIn(_In):
    check_session_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None


class ThreadOut(BaseModel):
    thread_id: str


class ConvertIn(_In):
    title: str = Field(min_length=2, max_length=100)
    case_type: enums.CaseType = enums.CaseType.FITNESS_REFUND


class CaseIn(_In):
    title: str = Field(min_length=2, max_length=100)
    case_type: enums.CaseType
    business_name: str | None = Field(default=None, max_length=100)


class EventIn(_In):
    occurred_on: date
    actor: enums.EventActor
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class EvidenceIn(_In):
    evidence_type: enums.EvidenceType
    title: str = Field(min_length=1, max_length=200)
    status: enums.EvidenceStatus
    document_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=4000)
    obtained_on: date | None = None


class PrepIn(_In):
    agency_code: str | None = Field(default=None, max_length=30)  # data/agencies.json 의 code


class PrepOut(BaseModel):
    case_id: uuid.UUID
    version: int  # 상담 준비자료 버전(사건별 1부터)


class EventOut(BaseModel):
    id: uuid.UUID
    occurred_on: date
    actor: enums.EventActor
    title: str
    description: str | None


class EvidenceOut(BaseModel):
    id: uuid.UUID
    evidence_type: enums.EvidenceType
    title: str
    status: enums.EvidenceStatus


class CaseDetailOut(BaseModel):
    id: uuid.UUID
    title: str
    case_type: enums.CaseType
    status: enums.CaseStatus
    business_name: str | None
    snapshot: dict[str, Any] | None
    events: list[EventOut]
    evidences: list[EvidenceOut]
    unverified: list[str]


# --- 계정(v0.9) ---------------------------------------------------------------
class _SecretIn(BaseModel):
    """비밀번호를 받는 입력. 앞뒤 공백도 비밀번호의 일부이므로 _In 과 달리 자르지 않는다."""

    model_config = ConfigDict(extra="forbid")


class SignUpIn(_SecretIn):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(
        min_length=8, max_length=64, description="영문 대·소문자·숫자·특수문자 3종 8자+ 또는 2종 10자+"
    )
    display_name: str | None = Field(default=None, max_length=50)


class LoginIn(_SecretIn):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=64)


class WithdrawIn(_SecretIn):
    password: str = Field(min_length=1, max_length=64, description="본인 재확인용 현재 비밀번호")


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105  # nosec B105 - 토큰 종류 이름, 비밀값 아님
    expires_in: int
