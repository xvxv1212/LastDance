"""사건 관리·상담 준비 유스케이스.

UC-08 점검 기록 → 사건 전환 / UC-09 신규 사건 생성 / UC-10 타임라인 추가 /
UC-11 증거 등록 / UC-12 상담 준비자료 생성 / UC-13 사건 상세 조회 / UC-14 사건 삭제 /
UC-18 사건에 PDF 추가 첨부
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, ClassVar

from app.application.ports.services import AgencyDirectory, Clock, FileStorage, UnitOfWork
from app.application.use_cases.common import audit, require_consent, store_document
from app.domain.entities import (
    Case,
    CaseEvent,
    CheckSession,
    ConsultationPrep,
    Evidence,
    ExtractedFact,
    InspectionReport,
    UnverifiedItem,
    new_id,
)
from app.domain.enums import (
    AuditAction,
    CaseStatus,
    CaseType,
    ConsentType,
    EventActor,
    EvidenceStatus,
    EvidenceType,
    FactKey,
    FactSourceKind,
    FactStatus,
    PrepStatus,
    ReportCategory,
)
from app.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from app.domain.policies import LegalExpressionPolicy, UploadPolicy


def _load_owned_case(uow: UnitOfWork, case_id: uuid.UUID, user_id: uuid.UUID) -> Case:
    """사건을 읽고 소유권을 확인하는 공통 절차(중복 제거)."""
    case = uow.cases.get(case_id)
    if case is None:
        raise NotFoundError("사건을 찾을 수 없습니다.")
    case.ensure_owned_by(user_id)
    return case


# ===========================================================================
# UC-08 점검 기록 → 사건 전환
# ===========================================================================
@dataclass(frozen=True)
class ConvertToCaseCommand:
    user_id: uuid.UUID
    session_id: uuid.UUID
    title: str
    case_type: CaseType = CaseType.FITNESS_REFUND


@dataclass(frozen=True)
class ConversionResult:
    case_id: uuid.UUID
    created: bool  # False 면 이미 전환돼 있던 사건(멱등 응답)


class ConvertSessionToCase:
    """승인된 점검 결과를 사건 기록으로 옮긴다.

    - 확인된 사실 → cases.verified_facts v1 (사건의 '정본 사실', 구 verified_snapshots)
    - 보고서의 미확인 정보 → cases.unverified_items (구 unverified_items 테이블)
    - 업로드 계약서 → 사건 증거(CONTRACT, 보유)
    - 계약일·해지요청일 → 타임라인 사건
    """

    # 타임라인으로 옮길 날짜형 사실과 그 표시 제목
    _DATE_EVENTS: ClassVar[dict[FactKey, tuple[str, EventActor]]] = {
        FactKey.CONTRACT_DATE: ("회원권 계약 체결", EventActor.USER),
        FactKey.CANCEL_REQUEST_DATE: ("해지·환불 요청", EventActor.USER),
    }

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: ConvertToCaseCommand) -> ConversionResult:
        """같은 점검을 여러 번 전환해도 사건은 1건만 존재한다(멱등, DB-02).

        - 이미 전환된 점검이면 기존 사건 ID 를 그대로 돌려준다(중복 클릭·재시도·체크포인트 재개 대비).
        - 동시에 두 요청이 들어와도 cases.origin_session_id UNIQUE 가 두 번째 INSERT 를 막고,
          그 요청은 롤백 후 먼저 만들어진 사건을 돌려받는다.
        """
        title = cmd.title.strip()
        if not 2 <= len(title) <= 100:
            raise ValidationError("사건 제목은 2~100자로 입력해 주세요.")
        now = self._clock.now()
        with self._uow as uow:
            session = uow.sessions.get(cmd.session_id)
            if session is None:
                raise NotFoundError("점검 기록을 찾을 수 없습니다.")
            session.ensure_owned_by(cmd.user_id)

            existing = uow.cases.find_by_origin_session(session.id)
            if existing is not None:
                existing.ensure_owned_by(cmd.user_id)  # 삭제된 사건이면 '없음'
                return ConversionResult(existing.id, created=False)

            report = uow.reports.latest_approved(session.id)
            if report is None:
                raise ConflictError("승인된 점검 보고서가 있어야 사건으로 전환할 수 있습니다.")
            try:
                case_id = self._convert(uow, session, report, title, cmd, now)
                uow.commit()
            except DuplicateError:
                uow.rollback()  # 동시 전환 경쟁에서 진 요청 → 먼저 생긴 사건을 돌려준다
                winner = uow.cases.find_by_origin_session(session.id)
                if winner is None:
                    raise
                return ConversionResult(winner.id, created=False)
        return ConversionResult(case_id, created=True)

    def _convert(
        self,
        uow: UnitOfWork,
        session: CheckSession,
        report: InspectionReport,
        title: str,
        cmd: ConvertToCaseCommand,
        now: datetime,
    ) -> uuid.UUID:
        """한 트랜잭션: 사건 생성(Snapshot v1·미확인 포함) → 증거·타임라인 복사 → 점검 상태 CONVERTED."""
        facts = [f for f in uow.facts.list_by_session(session.id) if f.is_confirmed]
        case = Case(
            id=new_id(),
            owner_id=cmd.user_id,
            title=title,
            case_type=cmd.case_type,
            status=CaseStatus.OPEN,
            origin_session_id=session.id,
            business_name=self._value_of(facts, FactKey.BUSINESS_NAME),
            created_at=now,
            updated_at=now,
        )
        # DEC-01: Snapshot 은 '사건 행의 컬럼'이므로 사건 없이 존재할 수 없다(case_id 항상 실재).
        case.set_snapshot(
            self._snapshot_payload(facts, report.id), report.id, session.facts_confirmed_at or now
        )
        for item in report.items:
            if item.category == ReportCategory.UNVERIFIED or item.is_held:
                # 미확인 정보와 근거 보류 항목은 상담 전 확인할 질문으로 옮긴다
                case.add_unverified(item.content, now)
        uow.cases.add(case)  # origin_session_id UNIQUE 위반 시 DuplicateError
        session.mark_converted(now)
        uow.sessions.save(session)

        for doc in uow.documents.list_by_session(session.id):
            doc.case_id = case.id
            uow.documents.save(doc)
            uow.case_records.add_evidence(
                Evidence(
                    id=new_id(),
                    case_id=case.id,
                    document_id=doc.id,
                    evidence_type=EvidenceType.CONTRACT,
                    title=doc.original_name,
                    status=EvidenceStatus.HELD,
                    created_at=now,
                    updated_at=now,
                )
            )
        for event in self._date_events(case.id, facts, now):
            uow.case_records.add_event(event)
        audit(
            uow,
            self._clock,
            cmd.user_id,
            AuditAction.CREATE,
            "cases",
            case.id,
            {"from_session": str(session.id)},
        )
        return case.id

    @staticmethod
    def _value_of(facts: list[ExtractedFact], key: FactKey) -> str | None:
        return next((f.value_text for f in facts if f.fact_key == key), None)

    @staticmethod
    def _snapshot_payload(facts: list[ExtractedFact], report_id: uuid.UUID) -> dict[str, Any]:
        """사건의 정본 사실(Verified Snapshot v1).

        v0.8 G3: 시민이 고친 값(MODIFIED)은 문서에서 나온 값이 아니므로 근거를 '진술(STATEMENT)'로 적고,
        AI 가 처음 붙인 근거 유형은 ai_source_kind 로 따로 남긴다(문서 원문 위치는 extracted_facts 에 그대로).
        """
        return {
            "source_report_id": str(report_id),
            "facts": [
                {
                    "key": f.fact_key,
                    "value": f.value_text,
                    "source_kind": (
                        FactSourceKind.STATEMENT if f.status == FactStatus.MODIFIED else f.source_kind
                    ),
                    "user_modified": f.status == FactStatus.MODIFIED,
                    "ai_source_kind": f.source_kind,
                    "normalized": f.value_normalized,
                    "fact_id": str(f.id),
                }
                for f in facts
            ],
        }

    def _date_events(self, case_id: uuid.UUID, facts: list[ExtractedFact], now: datetime) -> list[CaseEvent]:
        events = []
        for f in facts:
            if f.fact_key not in self._DATE_EVENTS or not f.value_normalized:
                continue
            raw = f.value_normalized.get("date")
            try:
                occurred = date.fromisoformat(str(raw))
            except ValueError:
                continue  # 날짜를 확정할 수 없으면 타임라인에 넣지 않음(추측 금지)
            title, actor = self._DATE_EVENTS[f.fact_key]
            events.append(
                CaseEvent(
                    id=new_id(),
                    case_id=case_id,
                    occurred_on=occurred,
                    actor=actor,
                    title=title,
                    description=f.value_text,
                    is_verified=True,
                    created_at=now,
                )
            )
        return events


# ===========================================================================
# UC-09 사전 점검 없이 신규 사건 생성
# ===========================================================================
@dataclass(frozen=True)
class CreateCaseCommand:
    user_id: uuid.UUID
    title: str
    case_type: CaseType
    business_name: str | None = None


class CreateCase:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: CreateCaseCommand) -> uuid.UUID:
        title = cmd.title.strip()
        if not 2 <= len(title) <= 100:
            raise ValidationError("사건 제목은 2~100자로 입력해 주세요.")
        now = self._clock.now()
        case = Case(
            id=new_id(),
            owner_id=cmd.user_id,
            title=title,
            case_type=cmd.case_type,
            status=CaseStatus.OPEN,
            business_name=cmd.business_name,
            created_at=now,
            updated_at=now,
        )
        with self._uow as uow:
            uow.cases.add(case)
            audit(uow, self._clock, cmd.user_id, AuditAction.CREATE, "cases", case.id)
            uow.commit()
        return case.id


# ===========================================================================
# UC-10 타임라인 사건 추가
# ===========================================================================
@dataclass(frozen=True)
class AddCaseEventCommand:
    user_id: uuid.UUID
    case_id: uuid.UUID
    occurred_on: date
    actor: EventActor
    title: str
    description: str | None = None


class AddCaseEvent:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: AddCaseEventCommand) -> uuid.UUID:
        now = self._clock.now()
        if cmd.occurred_on > now.date():
            raise ValidationError("미래 날짜의 사건은 기록할 수 없습니다.")
        if not cmd.title.strip():
            raise ValidationError("사건 내용을 입력해 주세요.")
        with self._uow as uow:
            case = _load_owned_case(uow, cmd.case_id, cmd.user_id)
            case.ensure_editable()
            event = CaseEvent(
                id=new_id(),
                case_id=case.id,
                occurred_on=cmd.occurred_on,
                actor=cmd.actor,
                title=cmd.title.strip()[:200],
                description=cmd.description,
                is_verified=True,
                created_at=now,
            )
            uow.case_records.add_event(event)
            uow.commit()
        return event.id


# ===========================================================================
# UC-18 사건에 PDF 추가 첨부
# ===========================================================================
@dataclass(frozen=True)
class AttachCaseDocumentCommand:
    user_id: uuid.UUID
    case_id: uuid.UUID
    filename: str
    mime_type: str
    content: bytes


class AttachCaseDocument:
    """사건에 PDF 를 추가로 첨부한다(신규 사건 직행·사건 진행 중 새로 생긴 증빙).

    점검 업로드(UC-03)와 같은 규칙을 그대로 적용한다:
    PDF·10MB·매직바이트 검증, 문서 업로드 동의, 소유자 확인(타인·삭제 사건은 404), 보관기한.
    추가로 종결된 사건에는 첨부할 수 없다(타임라인·증거 추가와 같은 규칙).
    이미지(문자 캡처 등)는 받지 않는다 — 2026.11.04 팀 결정(A안: 증빙은 항목으로 등록).
    첨부한 파일은 사실 추출(N1)·외부 AI 전송 대상이 아니며, 증거 등록(UC-11)에서 연결해 쓴다.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        storage: FileStorage,
        policy: UploadPolicy,
        retention_days: int = 180,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._storage = storage
        self._policy = policy
        self._retention_days = retention_days

    def execute(self, cmd: AttachCaseDocumentCommand) -> uuid.UUID:
        self._policy.validate(cmd.filename, cmd.mime_type, cmd.content)
        with self._uow as uow:
            require_consent(uow, cmd.user_id, ConsentType.SENSITIVE_DOCUMENT)
            case = _load_owned_case(uow, cmd.case_id, cmd.user_id)
            case.ensure_editable()
            return store_document(
                uow,
                self._clock,
                self._storage,
                owner_id=cmd.user_id,
                filename=cmd.filename,
                mime_type=cmd.mime_type,
                content=cmd.content,
                retention_days=self._retention_days,
                case_id=case.id,
            )


# ===========================================================================
# UC-11 증거 등록
# ===========================================================================
@dataclass(frozen=True)
class RegisterEvidenceCommand:
    user_id: uuid.UUID
    case_id: uuid.UUID
    evidence_type: EvidenceType
    title: str
    status: EvidenceStatus
    document_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None
    description: str | None = None
    obtained_on: date | None = None


class RegisterEvidence:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: RegisterEvidenceCommand) -> uuid.UUID:
        if cmd.status == EvidenceStatus.HELD and cmd.document_id is None and cmd.obtained_on is None:
            raise ValidationError("보유 증거는 첨부 문서 또는 확보일 중 하나가 필요합니다.")
        now = self._clock.now()
        with self._uow as uow:
            case = _load_owned_case(uow, cmd.case_id, cmd.user_id)
            case.ensure_editable()
            if cmd.document_id is not None:
                doc = uow.documents.get(cmd.document_id)
                if doc is None:
                    raise NotFoundError("문서를 찾을 수 없습니다.")
                doc.ensure_owned_by(cmd.user_id)  # 타인 문서를 내 증거로 연결하는 것을 차단
                if doc.case_id is not None and doc.case_id != case.id:
                    raise ValidationError("다른 사건에 연결된 문서입니다.")
                if doc.case_id is None:
                    # 같은 사용자의 미연결 문서는 이 사건에 귀속시킨다(DB 복합 FK: 증거·문서 같은 사건)
                    doc.case_id = case.id
                    uow.documents.save(doc)
            if cmd.event_id is not None and cmd.event_id not in {
                e.id for e in uow.case_records.list_events(case.id)
            }:
                raise ValidationError("이 사건의 타임라인 항목이 아닙니다.")
            evidence = Evidence(
                id=new_id(),
                case_id=case.id,
                evidence_type=cmd.evidence_type,
                title=cmd.title.strip()[:200],
                status=cmd.status,
                document_id=cmd.document_id,
                event_id=cmd.event_id,
                description=cmd.description,
                obtained_on=cmd.obtained_on,
                created_at=now,
                updated_at=now,
            )
            uow.case_records.add_evidence(evidence)
            uow.commit()
        return evidence.id


# ===========================================================================
# UC-12 상담 준비자료 생성
# ===========================================================================
@dataclass(frozen=True)
class PrepareConsultationCommand:
    user_id: uuid.UUID
    case_id: uuid.UUID
    agency_code: str | None = None


class PrepareConsultation:
    """사건 경위 요약 + 증빙 체크리스트 + 문의 전 확인 질문을 만든다.

    LLM 문장 대신 '사용자가 확인한 기록'만으로 결정적으로(deterministic) 조립하여
    상담기관에 전달될 자료에 추측이 섞이지 않게 한다.
    v0.6: 결과는 cases.consultation_prep(최신본 JSON)에 저장하고 버전 번호를 돌려준다.
    """

    def __init__(
        self, uow: UnitOfWork, clock: Clock, legal_policy: LegalExpressionPolicy, agencies: AgencyDirectory
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._legal = legal_policy
        self._agencies = agencies

    def execute(self, cmd: PrepareConsultationCommand) -> int:
        now = self._clock.now()
        with self._uow as uow:
            case = _load_owned_case(uow, cmd.case_id, cmd.user_id)
            events = sorted(uow.case_records.list_events(case.id), key=lambda e: e.occurred_on)
            if not events:
                raise ValidationError("타임라인이 1건 이상 있어야 상담 준비자료를 만들 수 있습니다.")
            agency = None
            if cmd.agency_code is not None:
                agency = self._agencies.get(cmd.agency_code)
                if agency is None or not agency.is_active:
                    raise NotFoundError("지원기관 정보를 찾을 수 없습니다.")

            evidences = uow.case_records.list_evidences(case.id)
            summary = self._summary(case, events)
            self._legal.validate(summary)

            prev = case.consultation_prep
            prep = ConsultationPrep(
                agency_code=agency.code if agency else None,
                agency_name=agency.name if agency else None,
                version=(prev.version if prev else 0) + 1,
                status=PrepStatus.DRAFT,
                summary_text=summary,
                checklist=[
                    {
                        "evidence_id": str(e.id),
                        "title": e.title,
                        "type": e.evidence_type,
                        "status": e.status,
                        "ready": e.status == EvidenceStatus.HELD,
                    }
                    for e in evidences
                ],
                questions=[f"확인 필요: {u.content}" for u in case.open_unverified],
                created_at=now,
            )
            case.start_preparing(prep, now)
            uow.cases.save(case)
            audit(
                uow,
                self._clock,
                cmd.user_id,
                AuditAction.CREATE,
                "cases.consultation_prep",
                case.id,
                {"version": prep.version},
            )
            uow.commit()
        return prep.version

    @staticmethod
    def _summary(case: Case, events: list[CaseEvent]) -> str:
        lines = [f"[사건] {case.title}"]
        if case.business_name:
            lines.append(f"[상대 사업자] {case.business_name}")
        lines.append("[경위]")
        lines += [f"- {e.occurred_on.isoformat()} {e.title}" for e in events]
        return "\n".join(lines)


# ===========================================================================
# UC-13 사건 상세 조회
# ===========================================================================
@dataclass(frozen=True)
class CaseDetail:
    case: Case
    events: list[CaseEvent]
    evidences: list[Evidence]

    @property
    def snapshot(self) -> dict[str, Any] | None:
        return self.case.verified_facts

    @property
    def unverified(self) -> list[UnverifiedItem]:
        return self.case.unverified_items


class GetCaseDetail:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, user_id: uuid.UUID, case_id: uuid.UUID) -> CaseDetail:
        with self._uow as uow:
            case = _load_owned_case(uow, case_id, user_id)
            detail = CaseDetail(
                case=case,
                events=sorted(uow.case_records.list_events(case.id), key=lambda e: e.occurred_on),
                evidences=uow.case_records.list_evidences(case.id),
            )
            # 민감 기록 열람도 감사 대상
            audit(uow, self._clock, user_id, AuditAction.READ_SENSITIVE, "cases", case.id)
            uow.commit()
        return detail


# ===========================================================================
# UC-14 사건 삭제(논리 삭제)
# ===========================================================================
class DeleteCase:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, user_id: uuid.UUID, case_id: uuid.UUID) -> None:
        now = self._clock.now()
        with self._uow as uow:
            case = _load_owned_case(uow, case_id, user_id)
            case.soft_delete(now)
            uow.cases.save(case)
            audit(uow, self._clock, user_id, AuditAction.DELETE, "cases", case.id)
            uow.commit()
