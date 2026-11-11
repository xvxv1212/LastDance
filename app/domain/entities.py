"""도메인 엔티티.

- 순수 dataclass 로 작성하여 DB·웹 프레임워크와 분리한다.
- 상태 변경은 반드시 엔티티 메서드를 통해서만 하도록 하여(캡슐화)
  '어떤 상태에서 어떤 상태로 갈 수 있는가'라는 업무 규칙을 한 곳에 모은다(SRP).
- 필드명은 테이블 컬럼명과 동일하게 맞춰, 인프라 계층의 매핑을 단순하게 유지한다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

from app.domain.enums import (
    AgentRunStatus,
    AuditAction,
    CaseStatus,
    CaseType,
    CitationCheck,
    ConfirmAction,
    ConsentType,
    EntryType,
    EventActor,
    EvidenceStatus,
    EvidenceType,
    FactKey,
    FactSourceKind,
    FactStatus,
    GuardCode,
    HoldReason,
    PrepStatus,
    ReportCategory,
    ReportStatus,
    SessionStatus,
    TransferBasis,
    UnverifiedStatus,
    UserRole,
    UserStatus,
)
from app.domain.errors import ConflictError, NotFoundError, ValidationError


def new_id() -> uuid.UUID:
    """식별자 생성. 순번(1,2,3…) 대신 UUID 를 써서 타인 자원 ID 추측(IDOR)을 어렵게 한다."""
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# 공통: 소유권 검사
# ---------------------------------------------------------------------------
def ensure_owner(owner_id: uuid.UUID, actor_id: uuid.UUID, resource: str) -> None:
    """요청자가 자원의 소유자인지 확인한다.

    타인 자원이면 403 대신 '없음(404)'으로 응답하여 자원 존재 여부 자체를 숨긴다.
    (기획서 품질 기준: '타 사용자 사건 접근 0건')
    """
    if owner_id != actor_id:
        raise NotFoundError(f"{resource}을(를) 찾을 수 없습니다.")


# ---------------------------------------------------------------------------
# 사용자·동의
# ---------------------------------------------------------------------------
@dataclass
class User:
    """회원 계정. v0.9: 이메일 + 비밀번호(Argon2id 해시) 로그인.

    탈퇴하면 행을 지우지 않고 식별정보만 지운 '빈 행'으로 남긴다. 감사 로그(audit_logs.actor_id)가
    이 행을 가리키기 때문이며, 감사 로그 보관기간이 지나면 파기 배치가 빈 행도 지운다.
    """

    id: uuid.UUID
    email: str | None  # 탈퇴하면 None
    password_hash: str | None  # 탈퇴하면 None
    display_name: str | None
    role: UserRole
    status: UserStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    failed_login_count: int = 0  # 연속 로그인 실패 횟수(성공하면 0)
    locked_until: datetime | None = None  # 이 시각까지 로그인 차단

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    def is_locked(self, now: datetime) -> bool:
        return self.locked_until is not None and _aware(self.locked_until) > now

    def record_login_failure(self, now: datetime, max_failures: int, lock_for: timedelta) -> None:
        """연속 실패가 max_failures 에 이르면 lock_for 동안 잠근다(안전성 확보조치 기준 제5조 취지)."""
        self.failed_login_count += 1
        if self.failed_login_count >= max_failures:
            self.locked_until = now + lock_for
            self.failed_login_count = 0
        self.updated_at = now

    def record_login_success(self, now: datetime) -> None:
        if self.failed_login_count or self.locked_until is not None:
            self.failed_login_count = 0
            self.locked_until = None
            self.updated_at = now

    def withdraw(self, now: datetime) -> None:
        """탈퇴: 상태 변경 + 식별정보 즉시 삭제(개인정보 보호법 제21조 '지체 없이 파기')."""
        if self.status == UserStatus.WITHDRAWN:
            raise ConflictError("이미 탈퇴한 계정입니다.")
        self.status = UserStatus.WITHDRAWN
        self.email = None  # 같은 이메일로 다시 가입하면 새 계정이 된다
        self.password_hash = None
        self.display_name = None
        self.failed_login_count = 0
        self.locked_until = None
        self.deleted_at = now
        self.updated_at = now


def _aware(value: datetime) -> datetime:
    """SQLite 는 시간대 없이 돌려주므로 UTC 로 간주한다(PostgreSQL 은 timestamptz)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass
class UserConsent:
    """동의 이력. 수정하지 않고 행을 추가(append-only)하여 '언제 무엇에 동의했는지'를 보존한다."""

    id: uuid.UUID
    user_id: uuid.UUID
    consent_type: ConsentType
    policy_version: str
    agreed: bool
    agreed_at: datetime
    withdrawn_at: datetime | None = None

    @property
    def is_effective(self) -> bool:
        return self.agreed and self.withdrawn_at is None


# ---------------------------------------------------------------------------
# AI 점검 세션
# ---------------------------------------------------------------------------
@dataclass
class CheckSession:
    """'AI 통합 점검' 1회분. 상황 입력 → 사실 추출 → 확인 → 보고서 → (사건 전환)."""

    id: uuid.UUID
    user_id: uuid.UUID
    entry_type: EntryType
    situation_text: str | None  # v0.8: PDF 만으로 시작하면 None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    facts_confirmed_at: datetime | None = None  # 사용자가 사실 전체를 승인한 시각(DB-01)
    thread_id: str | None = None  # LangGraph thread_id (v0.6: 구 agent_threads 테이블 대신 컬럼)
    deleted_at: datetime | None = None

    # 점검↔사건 관계는 cases.origin_session_id(UNIQUE) 한 곳에만 저장한다(DB-02 관계 단일화).
    # 허용되는 상태 전이 표. 표에 없는 전이는 모두 거부한다.
    _TRANSITIONS: ClassVar[dict[SessionStatus, set[SessionStatus]]] = {
        SessionStatus.DRAFT: {SessionStatus.FACTS_EXTRACTED, SessionStatus.ABANDONED},
        SessionStatus.FACTS_EXTRACTED: {
            SessionStatus.FACTS_EXTRACTED,  # 재추출 허용
            SessionStatus.FACTS_CONFIRMED,
            SessionStatus.ABANDONED,
        },
        # v0.8 G4: 보고서 승인 전에는 확인을 마친 사실도 다시 고칠 수 있다(확인 완료 → 확인 완료).
        # 보고서 승인(REPORTED) 이후에는 표에 없으므로 계속 거부된다.
        SessionStatus.FACTS_CONFIRMED: {
            SessionStatus.REPORTED,
            SessionStatus.FACTS_EXTRACTED,
            SessionStatus.FACTS_CONFIRMED,
        },
        SessionStatus.REPORTED: {SessionStatus.CONVERTED, SessionStatus.REPORTED},
        SessionStatus.CONVERTED: set(),
        SessionStatus.ABANDONED: set(),
    }

    def _move(self, to: SessionStatus, now: datetime) -> None:
        if to not in self._TRANSITIONS[self.status]:
            raise ConflictError(f"점검 세션을 {self.status} → {to} 로 변경할 수 없습니다.")
        self.status = to
        self.updated_at = now

    def ensure_owned_by(self, actor_id: uuid.UUID) -> None:
        if self.deleted_at is not None:
            raise NotFoundError("점검 기록을 찾을 수 없습니다.")
        ensure_owner(self.user_id, actor_id, "점검 기록")

    def mark_facts_extracted(self, now: datetime) -> None:
        self._move(SessionStatus.FACTS_EXTRACTED, now)

    def mark_facts_confirmed(self, facts: list[ExtractedFact], now: datetime) -> None:
        """모든 사실이 사용자 처리(확인·수정·거부·모름 인지)를 거쳐야 다음 단계로 갈 수 있다.

        v0.11: 값이 확인된 사실(CONFIRMED·MODIFIED)이 1건 이상 있어야 한다.
        전부 '모름'이거나 전부 거부된 점검은 보고서로 쓸 사실이 없으므로 넘어가지 않는다.
        """
        pending = [f for f in facts if f.status == FactStatus.EXTRACTED]
        if pending:
            raise ValidationError(f"아직 확인하지 않은 사실이 {len(pending)}건 있습니다.")
        if not any(f.is_confirmed for f in facts):
            raise ConflictError("값이 확인된 사실이 1건 이상 있어야 보고서 단계로 갈 수 있습니다.")
        self._move(SessionStatus.FACTS_CONFIRMED, now)
        self.facts_confirmed_at = now

    def reopen_facts(self, now: datetime) -> None:
        """v0.11: 확인 완료 뒤 '값이 확인된 사실'이 0건이 되면 확인 대기로 되돌린다(승인 시각도 비움).

        보고서 승인(REPORTED) 이후에는 전이표에 없으므로 거부된다(기존 규칙 유지).
        """
        self._move(SessionStatus.FACTS_EXTRACTED, now)
        self.facts_confirmed_at = None

    def mark_reported(self, now: datetime) -> None:
        self._move(SessionStatus.REPORTED, now)

    def mark_converted(self, now: datetime) -> None:
        """REPORTED 에서만 CONVERTED 로 갈 수 있다. 중복 전환 처리는 유스케이스가 멱등하게 한다."""
        self._move(SessionStatus.CONVERTED, now)


@dataclass
class Document:
    """업로드 파일의 메타데이터. 파일 본문은 DB 가 아닌 별도 저장소(storage_key)에 보관한다."""

    id: uuid.UUID
    owner_id: uuid.UUID
    original_name: str
    mime_type: str
    size_bytes: int
    storage_key: str
    sha256: str
    created_at: datetime
    retention_until: date
    check_session_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    page_count: int | None = None
    deleted_at: datetime | None = None

    def ensure_owned_by(self, actor_id: uuid.UUID) -> None:
        if self.deleted_at is not None:
            raise NotFoundError("문서를 찾을 수 없습니다.")
        ensure_owner(self.owner_id, actor_id, "문서")


@dataclass
class ExtractedFact:
    """1차 추론(ExtractFacts)이 문서·진술에서 뽑아낸 사실 1건과 그 출처 위치."""

    id: uuid.UUID
    check_session_id: uuid.UUID
    fact_key: FactKey
    value_text: str | None  # 근거가 '미확인(UNKNOWN)'이면 값이 없다
    source_kind: FactSourceKind
    status: FactStatus
    created_at: datetime
    updated_at: datetime
    value_normalized: dict[str, Any] | None = None
    document_id: uuid.UUID | None = None
    page_no: int | None = None
    offset_start: int | None = None  # 원문 문자 오프셋(빅데이터 분석 정의서 C1 스키마)
    offset_end: int | None = None
    source_span: str | None = None
    has_conflict: bool = False
    ai_value_text: str | None = None  # AI 가 처음 추출한 값. 사용자가 고쳐도 바꾸지 않는다(AI-01)
    confirmed_by: uuid.UUID | None = None
    confirmed_at: datetime | None = None

    def apply(self, action: ConfirmAction, new_value: str | None, actor_id: uuid.UUID, now: datetime) -> None:
        """사용자 확인 결과를 반영한다.

        v0.6: 확인 이력 테이블(fact_confirmations) 대신 이 행의 확인 컬럼에 '마지막 처리'를 남긴다.
        - AI 원래 값은 ai_value_text 에 그대로 있으므로 '사람이 바꾼 값'과 언제든 비교할 수 있다.
        - 여러 번 고친 경우의 중간 값은 남기지 않는다(처리 사실·시각은 audit_logs 에 남음).
        - v0.11 ACKNOWLEDGE_UNKNOWN: confirmed_by·confirmed_at 은 '모름을 인지 처리한 사람·시각'이다
          (사실을 확인했다는 뜻이 아니다). 값·근거 유형은 UNKNOWN/NULL 그대로 둔다.
        """
        if action == ConfirmAction.CONFIRM:
            if self.source_kind == FactSourceKind.UNKNOWN:
                raise ValidationError("미확인 항목은 값을 입력(수정)해야 확인할 수 있습니다.")
            self.status = FactStatus.CONFIRMED
        elif action == ConfirmAction.MODIFY:
            if not new_value or not new_value.strip():
                raise ValidationError("수정할 값을 입력해 주세요.")
            self.value_text = new_value.strip()
            self.value_normalized = None  # 사용자가 바꾼 값은 재정규화가 필요하므로 비운다
            if self.source_kind == FactSourceKind.UNKNOWN:
                self.source_kind = FactSourceKind.STATEMENT  # 시민이 직접 알려준 값 = 진술
            self.status = FactStatus.MODIFIED
        elif action == ConfirmAction.ACKNOWLEDGE_UNKNOWN:
            # v0.11: '모름 인지'. 값을 만들지 않는다 — 빈 문자열·0·임의 날짜 같은 대체값도 받지 않는다.
            if new_value is not None:
                raise ValidationError("'모름' 처리에는 값을 보내지 않습니다.")
            if self.source_kind != FactSourceKind.UNKNOWN or self.value_text is not None:
                raise ValidationError("미확인(UNKNOWN) 항목만 '모름'으로 처리할 수 있습니다.")
            self.status = FactStatus.ACKNOWLEDGED_UNKNOWN
        else:
            self.status = FactStatus.REJECTED
        self.has_conflict = False
        self.confirmed_by = actor_id
        self.confirmed_at = now
        self.updated_at = now

    @property
    def is_confirmed(self) -> bool:
        return self.status in (FactStatus.CONFIRMED, FactStatus.MODIFIED)


# ---------------------------------------------------------------------------
# 점검 보고서 (애그리거트: 보고서 → 항목 → 인용)
#   v0.6: 항목·인용은 inspection_reports.items JSON 한 칸에 함께 저장한다(보고서는 한 번에 쓰고 한 번에 읽음).
# ---------------------------------------------------------------------------
@dataclass
class ReportCitation:
    requested_chunk_ref: str  # AI 가 인용한 청크 ID 원문(가짜 출처 추적용)
    quoted_text: str
    check_result: CitationCheck  # 서버 검증 결과(AI-03)
    chunk_id: str | None = None  # 벡터 지식 DB 의 청크 ID. 가짜 출처(CHUNK_NOT_FOUND)면 None
    # 인용 '당시'의 출처 정보 사본: 지식 DB 가 갱신·폐지돼도 보고서가 보여 준 근거는 그대로 남는다
    source_title: str | None = None
    source_url: str | None = None
    article_ref: str | None = None
    effective_date: date | None = None

    @property
    def is_verified(self) -> bool:
        return self.check_result == CitationCheck.VERIFIED


@dataclass
class ReportItem:
    id: uuid.UUID
    category: ReportCategory
    content: str
    sort_order: int
    fact_id: uuid.UUID | None = None
    hold_reason: HoldReason | None = None  # 값이 있으면 '보류' — 확정 내용으로 표시하지 않음
    citations: list[ReportCitation] = field(default_factory=list)

    @property
    def is_held(self) -> bool:
        return self.hold_reason is not None


@dataclass
class InspectionReport:
    id: uuid.UUID
    check_session_id: uuid.UUID
    version: int
    status: ReportStatus
    summary_text: str
    created_at: datetime
    approved_at: datetime | None = None
    items: list[ReportItem] = field(default_factory=list)

    def approve(self, now: datetime) -> None:
        if self.status != ReportStatus.DRAFT:
            raise ConflictError("초안 상태의 보고서만 승인할 수 있습니다.")
        self.status = ReportStatus.APPROVED
        self.approved_at = now


# ---------------------------------------------------------------------------
# 사건
# ---------------------------------------------------------------------------
@dataclass
class Case:
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    case_type: CaseType
    status: CaseStatus
    created_at: datetime
    updated_at: datetime
    origin_session_id: uuid.UUID | None = None
    business_name: str | None = None
    thread_id: str | None = None
    # 확인 사실 스냅샷(v0.6: 구 verified_snapshots → 컬럼). 세 값은 함께 채워진다(DB CHECK)
    verified_facts: dict[str, Any] | None = None
    snapshot_version: int | None = None
    snapshot_report_id: uuid.UUID | None = None
    snapshot_approved_at: datetime | None = None
    unverified_items: list[UnverifiedItem] = field(default_factory=list)  # 구 unverified_items
    consultation_prep: ConsultationPrep | None = None  # 최신 상담 준비자료(구 consultation_preps)
    closed_at: datetime | None = None
    deleted_at: datetime | None = None

    def set_snapshot(
        self, payload: dict[str, Any], report_id: uuid.UUID | None, approved_at: datetime
    ) -> None:
        """확인 사실 스냅샷을 새 버전으로 바꾼다(DEC-01: 사건이 있어야만 존재)."""
        self.verified_facts = payload
        self.snapshot_version = (self.snapshot_version or 0) + 1
        self.snapshot_report_id = report_id
        self.snapshot_approved_at = approved_at

    def add_unverified(self, content: str, now: datetime) -> None:
        self.unverified_items.append(
            UnverifiedItem(id=new_id(), content=content, status=UnverifiedStatus.OPEN, created_at=now)
        )

    @property
    def open_unverified(self) -> list[UnverifiedItem]:
        return [u for u in self.unverified_items if u.status == UnverifiedStatus.OPEN]

    def ensure_owned_by(self, actor_id: uuid.UUID) -> None:
        if self.deleted_at is not None:
            raise NotFoundError("사건을 찾을 수 없습니다.")
        ensure_owner(self.owner_id, actor_id, "사건")

    def ensure_editable(self) -> None:
        if self.status == CaseStatus.CLOSED:
            raise ConflictError("종결된 사건은 수정할 수 없습니다.")

    def start_preparing(self, prep: ConsultationPrep, now: datetime) -> None:
        """새 상담 준비자료를 최신본으로 저장한다(이전 버전은 버전 번호와 audit_logs 로 추적)."""
        self.ensure_editable()
        self.consultation_prep = prep
        if self.status == CaseStatus.OPEN:
            self.status = CaseStatus.PREPARING
        self.updated_at = now

    def soft_delete(self, now: datetime) -> None:
        """논리 삭제. 실제 파일·개인정보 파기는 보관기한 배치가 수행한다."""
        self.deleted_at = now
        self.updated_at = now


@dataclass
class CaseEvent:
    id: uuid.UUID
    case_id: uuid.UUID
    occurred_on: date
    actor: EventActor
    title: str
    created_at: datetime
    description: str | None = None
    is_verified: bool = False


@dataclass
class Evidence:
    id: uuid.UUID
    case_id: uuid.UUID
    evidence_type: EvidenceType
    title: str
    status: EvidenceStatus
    created_at: datetime
    updated_at: datetime
    event_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    description: str | None = None
    obtained_on: date | None = None


@dataclass
class UnverifiedItem:
    """사건의 미확인 정보(상담 전 확인할 질문의 원천). cases.unverified_items JSON 의 한 원소."""

    id: uuid.UUID
    content: str
    status: UnverifiedStatus
    created_at: datetime
    resolved_at: datetime | None = None


@dataclass
class SupportAgency:
    """공공 지원기관 기준정보. v0.6: DB 테이블이 아니라 설정 파일(data/agencies.json)에서 읽는다."""

    code: str
    name: str
    agency_type: str
    url: str
    is_active: bool
    phone: str | None = None
    description: str | None = None
    eligibility: str | None = None
    last_verified_on: date | None = None


@dataclass
class ConsultationPrep:
    """상담 준비자료. 사건 경위 요약 + 증빙 체크리스트 + 문의 전 확인 질문. cases.consultation_prep JSON."""

    version: int
    status: PrepStatus
    summary_text: str
    checklist: list[dict[str, Any]]
    questions: list[str]
    created_at: datetime
    agency_code: str | None = None
    agency_name: str | None = None  # 작성 당시 기관명 사본(설정 파일이 바뀌어도 자료는 그대로)
    approved_at: datetime | None = None


@dataclass
class AuditLog:
    """감사 로그. 누가 언제 어떤 민감 자원에 무엇을 했는지 남긴다(수정·삭제 불가)."""

    id: uuid.UUID
    action: AuditAction
    target_type: str
    target_id: uuid.UUID | None
    created_at: datetime
    actor_id: uuid.UUID | None = None
    ip_hash: str | None = None
    detail: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# AI 호출 기록 (v0.6: 구 agent_threads·agent_runs 테이블 대체)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AiCallRecord:
    """외부 AI 호출 1회의 '감사용' 기록. audit_logs(AI_CALL).detail 로 저장한다.

    - 입력 원문·출력 원문은 담지 않는다(코드·수치만).
    - 토큰·지연시간 같은 성능·비용 값은 감사 대상이 아니므로 애플리케이션 로그로 보낸다.
    - 구 agent_runs 의 DB CHECK 2개를 생성 시점 검사로 옮겼다(JSON 안쪽은 DB CHECK 를 걸기 어렵다).
    """

    node_name: str
    status: AgentRunStatus
    external_api: bool
    transfer_basis: TransferBasis = TransferBasis.NOT_APPLICABLE
    error_code: str | None = None
    model_name: str | None = None

    def __post_init__(self) -> None:
        # 외부 API 로 보냈다면 처리 근거가 반드시 있어야 한다(근거 없는 외부 전송 0건)
        if self.external_api and self.transfer_basis == TransferBasis.NOT_APPLICABLE:
            raise ValidationError("외부 전송 기록에는 처리 근거(동의·위탁)가 필요합니다.")
        # 차단 기록은 외부 전송이 없어야 하고 사유 코드가 있어야 한다(fail-closed 증거)
        if self.status == AgentRunStatus.BLOCKED and (self.external_api or not self.error_code):
            raise ValidationError("차단 기록은 외부 전송 없이 사유 코드만 가져야 합니다.")

    @classmethod
    def blocked(cls, node_name: str, code: GuardCode) -> AiCallRecord:
        return cls(node_name, AgentRunStatus.BLOCKED, external_api=False, error_code=code)

    def to_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "node": self.node_name,
            "status": self.status,
            "external_api": self.external_api,
            "transfer_basis": self.transfer_basis,
        }
        if self.error_code:
            detail["code"] = self.error_code
        if self.model_name:
            detail["model"] = self.model_name
        return detail
