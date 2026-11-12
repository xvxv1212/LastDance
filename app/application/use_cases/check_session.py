"""AI 통합 점검 관련 유스케이스.

UC-01 동의 기록 / UC-02 점검 시작 / UC-03 계약서 업로드 /
UC-04 추출 사실 등록 / UC-05 사실 확인·수정
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.application.ports.services import Clock, FileStorage, UnitOfWork
from app.application.use_cases.common import audit, require_consent, store_document
from app.domain.entities import (
    CheckSession,
    ExtractedFact,
    UserConsent,
    new_id,
)
from app.domain.enums import (
    AuditAction,
    ConfirmAction,
    ConsentType,
    EntryType,
    FactKey,
    FactSourceKind,
    FactStatus,
    SessionStatus,
)
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.domain.policies import PersonalInfoMasker, UploadPolicy


# ===========================================================================
# UC-01 개인정보·문서·국외이전 동의 기록
# ===========================================================================
@dataclass(frozen=True)
class RecordConsentCommand:
    user_id: uuid.UUID
    consent_type: ConsentType
    policy_version: str
    agreed: bool


class RecordConsent:
    """동의/철회를 '새 행 추가' 방식으로 기록한다(이력 보존)."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: RecordConsentCommand) -> uuid.UUID:
        now = self._clock.now()
        consent = UserConsent(
            id=new_id(),
            user_id=cmd.user_id,
            consent_type=cmd.consent_type,
            policy_version=cmd.policy_version,
            agreed=cmd.agreed,
            agreed_at=now,
            withdrawn_at=None if cmd.agreed else now,
        )
        with self._uow as uow:
            uow.consents.add(consent)
            audit(
                uow,
                self._clock,
                cmd.user_id,
                AuditAction.CONSENT,
                "user_consents",
                consent.id,
                {"type": cmd.consent_type, "agreed": cmd.agreed, "version": cmd.policy_version},
            )
            uow.commit()
        return consent.id


# ===========================================================================
# UC-02 생활언어로 점검 시작
# ===========================================================================
@dataclass(frozen=True)
class StartCheckSessionCommand:
    user_id: uuid.UUID
    entry_type: EntryType
    situation_text: str | None = None  # v0.8: 없으면 'PDF 로 시작' — 사실 등록 전에 PDF 가 있어야 한다


class StartCheckSession:
    MIN_LEN, MAX_LEN = 5, 4000

    def __init__(self, uow: UnitOfWork, clock: Clock, masker: PersonalInfoMasker) -> None:
        self._uow = uow
        self._clock = clock
        self._masker = masker

    def execute(self, cmd: StartCheckSessionCommand) -> uuid.UUID:
        text: str | None = None
        if cmd.situation_text is not None:
            text = cmd.situation_text.strip()
            if not (self.MIN_LEN <= len(text) <= self.MAX_LEN):
                raise ValidationError(f"상황 설명은 {self.MIN_LEN}~{self.MAX_LEN}자로 입력해 주세요.")
        now = self._clock.now()
        session = CheckSession(
            id=new_id(),
            user_id=cmd.user_id,
            entry_type=cmd.entry_type,
            situation_text=self._masker.mask(text) if text else None,  # 고유식별정보는 저장 전에 가림
            status=SessionStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        with self._uow as uow:
            require_consent(uow, cmd.user_id, ConsentType.PRIVACY_REQUIRED)
            uow.sessions.add(session)
            audit(uow, self._clock, cmd.user_id, AuditAction.CREATE, "check_sessions", session.id)
            uow.commit()
        return session.id


# ===========================================================================
# UC-03 계약서(PDF) 업로드
# ===========================================================================
@dataclass(frozen=True)
class UploadDocumentCommand:
    user_id: uuid.UUID
    session_id: uuid.UUID
    filename: str
    mime_type: str
    content: bytes


class UploadContractDocument:
    """파일 검증 → 저장소 저장 → 메타데이터 DB 기록.

    DB 커밋이 실패하면 이미 저장한 파일을 지워 '고아 파일'이 남지 않게 한다.
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

    def execute(self, cmd: UploadDocumentCommand) -> uuid.UUID:
        self._policy.validate(cmd.filename, cmd.mime_type, cmd.content)
        with self._uow as uow:
            require_consent(uow, cmd.user_id, ConsentType.SENSITIVE_DOCUMENT)
            session = uow.sessions.get(cmd.session_id)
            if session is None:
                raise NotFoundError("점검 기록을 찾을 수 없습니다.")
            session.ensure_owned_by(cmd.user_id)
            return store_document(
                uow,
                self._clock,
                self._storage,
                owner_id=cmd.user_id,
                filename=cmd.filename,
                mime_type=cmd.mime_type,
                content=cmd.content,
                retention_days=self._retention_days,
                check_session_id=session.id,
            )


# ===========================================================================
# UC-04 1차 추론 결과(추출 사실) 등록  ※ 호출 주체: LangGraph ExtractFacts 노드
# ===========================================================================
@dataclass(frozen=True)
class FactInput:
    fact_key: FactKey
    value_text: str | None  # 근거 유형이 UNKNOWN(미확인)이면 None
    source_kind: FactSourceKind
    value_normalized: dict[str, Any] | None = None
    document_id: uuid.UUID | None = None
    page_no: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    source_span: str | None = None


@dataclass(frozen=True)
class RecordExtractedFactsCommand:
    user_id: uuid.UUID
    session_id: uuid.UUID
    facts: tuple[FactInput, ...]


class RecordExtractedFacts:
    """AI 가 추출한 사실을 저장하고, 같은 항목에 서로 다른 값이 있으면 '충돌'로 표시한다."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: RecordExtractedFactsCommand) -> list[uuid.UUID]:
        if not cmd.facts:
            raise ValidationError("등록할 사실이 없습니다.")
        now = self._clock.now()
        with self._uow as uow:
            session = uow.sessions.get(cmd.session_id)
            if session is None:
                raise NotFoundError("점검 기록을 찾을 수 없습니다.")
            session.ensure_owned_by(cmd.user_id)
            # v0.8: 상황 설명 없이 시작한 점검은 근거가 될 계약서 PDF 가 먼저 있어야 한다
            if not session.situation_text and not uow.documents.list_by_session(session.id):
                raise ValidationError("상황 설명이나 계약서 PDF 중 하나는 있어야 사실을 등록할 수 있습니다.")
            self._validate_sources(uow, cmd)

            facts = [self._to_entity(cmd.session_id, f, now) for f in cmd.facts]
            self._flag_conflicts(facts)
            uow.facts.add_many(facts)
            session.mark_facts_extracted(now)
            uow.sessions.save(session)
            uow.commit()
        return [f.id for f in facts]

    @staticmethod
    def _validate_sources(uow: UnitOfWork, cmd: RecordExtractedFactsCommand) -> None:
        """문서 출처 사실은 '같은 세션의 문서'를 가리켜야 한다(다른 사건 자료 혼입 금지)."""
        session_doc_ids = {d.id for d in uow.documents.list_by_session(cmd.session_id)}
        for f in cmd.facts:
            has_value = bool(f.value_text and f.value_text.strip())
            if f.source_kind == FactSourceKind.UNKNOWN and has_value:
                raise ValidationError("미확인 항목에는 값을 넣지 않습니다(추정값 금지).")
            if f.source_kind == FactSourceKind.UNKNOWN and any(
                value is not None
                for value in (
                    f.value_text,
                    f.value_normalized,
                    f.document_id,
                    f.page_no,
                    f.offset_start,
                    f.offset_end,
                    f.source_span,
                )
            ):
                # 모르는 값에 정규화값이나 원문 위치만 붙여도 근거가 있는 것처럼 보일 수 있다.
                raise ValidationError("UNKNOWN 항목에는 값·정규화값·근거 위치를 넣지 않습니다.")
            if f.source_kind != FactSourceKind.UNKNOWN and not has_value:
                raise ValidationError(
                    "근거가 있는 사실은 값이 필요합니다. 근거가 없으면 UNKNOWN 으로 보내세요."
                )
            if f.source_kind == FactSourceKind.DOCUMENT and f.document_id not in session_doc_ids:
                raise ValidationError("문서 출처 사실은 이 점검에 업로드된 문서를 가리켜야 합니다.")
            if f.source_kind != FactSourceKind.DOCUMENT and f.document_id is not None:
                raise ValidationError("문서 출처가 아닌 사실에는 document_id 를 넣지 않습니다.")
            if (f.offset_start is None) != (f.offset_end is None) or (
                f.offset_start is not None
                and f.offset_end is not None
                and not 0 <= f.offset_start <= f.offset_end
            ):
                raise ValidationError("원문 오프셋은 시작·끝을 함께, 시작 ≤ 끝으로 보내야 합니다.")

    @staticmethod
    def _to_entity(session_id: uuid.UUID, f: FactInput, now: Any) -> ExtractedFact:
        value = f.value_text.strip() if f.value_text else None
        return ExtractedFact(
            id=new_id(),
            check_session_id=session_id,
            fact_key=f.fact_key,
            value_text=value,
            ai_value_text=value,  # AI 원래 값 보존(사용자가 고쳐도 이 컬럼은 그대로, AI-01)
            value_normalized=f.value_normalized,
            source_kind=f.source_kind,
            document_id=f.document_id,
            page_no=f.page_no,
            offset_start=f.offset_start,
            offset_end=f.offset_end,
            source_span=f.source_span,
            status=FactStatus.EXTRACTED,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _flag_conflicts(facts: list[ExtractedFact]) -> None:
        """예: 계약서에는 결제금액 120만원, 진술에는 100만원 → 둘 다 충돌 표시 후 사용자에게 질문."""
        by_key: dict[FactKey, set[str]] = {}
        for f in facts:
            if f.fact_key != FactKey.OTHER and f.value_text is not None:
                by_key.setdefault(f.fact_key, set()).add(f.value_text)
        for f in facts:
            if len(by_key.get(f.fact_key, set())) > 1:
                f.has_conflict = True


# ===========================================================================
# UC-05 사실 확인·수정 (Human-in-the-loop)
# ===========================================================================
@dataclass(frozen=True)
class ConfirmFactCommand:
    user_id: uuid.UUID
    fact_id: uuid.UUID
    action: ConfirmAction
    new_value: str | None = None


class ConfirmFact:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: ConfirmFactCommand) -> SessionStatus:
        now = self._clock.now()
        with self._uow as uow:
            fact = uow.facts.get(cmd.fact_id)
            if fact is None:
                raise NotFoundError("사실 항목을 찾을 수 없습니다.")
            session = uow.sessions.get(fact.check_session_id)
            if session is None:
                raise NotFoundError("점검 기록을 찾을 수 없습니다.")
            session.ensure_owned_by(cmd.user_id)

            if session.status not in (SessionStatus.FACTS_EXTRACTED, SessionStatus.FACTS_CONFIRMED):
                raise ConflictError("보고서 승인 전의 점검 사실만 확인·수정할 수 있습니다.")
            fact.apply(cmd.action, cmd.new_value, cmd.user_id, now)
            uow.facts.save(fact)
            # 처리 '사실'(누가·언제·무엇을)은 감사 로그에 남긴다. 값 원문은 남기지 않는다.
            audit(
                uow,
                self._clock,
                cmd.user_id,
                AuditAction.UPDATE,
                "extracted_facts",
                fact.id,
                {"action": cmd.action, "fact_key": fact.fact_key},
            )
            # 미처리(EXTRACTED) 0건 + 값이 확인된 사실 1건 이상이면 세션을 다음 단계로 넘긴다(v0.11).
            # 전부 '모름'·거부뿐이면 넘기지 않고, 이미 넘어가 있었다면 확인 대기로 되돌린다.
            all_facts = uow.facts.list_by_session(session.id)
            pending = any(f.status == FactStatus.EXTRACTED for f in all_facts)
            if not pending and any(f.is_confirmed for f in all_facts):
                session.mark_facts_confirmed(all_facts, now)
                uow.sessions.save(session)
            elif session.status != SessionStatus.FACTS_EXTRACTED:
                session.reopen_facts(now)  # 보고서 승인 이후(REPORTED~)면 여기서 409
                uow.sessions.save(session)
            uow.commit()
            return session.status


# ===========================================================================
# UC-05b 점검의 사실 목록 조회(v0.11) — 시민 본인만. 확인 화면이 다시 읽을 때 쓴다.
# ===========================================================================
@dataclass(frozen=True)
class SessionFacts:
    session: CheckSession
    facts: list[ExtractedFact]


class ListSessionFacts:
    """한 점검의 사실 전체를 돌려준다. AI 최초값(ai_value_text)과 사용자 현재값(value_text)을 함께 준다.

    민감 기록 열람이므로 감사 로그(READ_SENSITIVE)를 남긴다. 값 원문은 로그에 넣지 않는다.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionFacts:
        with self._uow as uow:
            session = uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("점검 기록을 찾을 수 없습니다.")
            session.ensure_owned_by(user_id)
            facts = sorted(uow.facts.list_by_session(session.id), key=lambda f: (f.created_at, str(f.id)))
            audit(
                uow,
                self._clock,
                user_id,
                AuditAction.READ_SENSITIVE,
                "check_sessions",
                session.id,
                {"view": "facts", "count": len(facts)},
            )
            uow.commit()
        return SessionFacts(session, facts)
