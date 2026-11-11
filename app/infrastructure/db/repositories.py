"""SQLAlchemy 기반 저장소 구현(포트의 어댑터).

- 도메인 엔티티 ↔ ORM 모델 변환은 이 파일 안에서만 일어난다.
  도메인은 ORM 을 모르고, ORM 모델은 업무 규칙을 갖지 않는다.
- 엔티티 필드명과 컬럼명을 같게 설계했으므로 범용 매퍼(_Mapper)로 반복 코드를 줄였다.
- v0.6: JSON 컬럼(보고서 항목, 사건의 미확인 정보·상담 준비자료)의 '모양'도 이 파일에서만 정한다.
  JSON 에 넣는 키 이름을 바꾸면 기존 행을 읽지 못하므로, 키를 바꿀 때는 마이그레이션이 필요하다.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, datetime
from typing import Any, Generic, TypeVar

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.ports import repositories as ports
from app.domain import entities as dom
from app.domain.enums import (
    CaseStatus,
    CitationCheck,
    ConsentType,
    HoldReason,
    PrepStatus,
    ReportCategory,
    ReportStatus,
    UnverifiedStatus,
    UserStatus,
)
from app.domain.errors import DuplicateError
from app.infrastructure.db import models as orm
from app.infrastructure.db.base import Base

EntityT = TypeVar("EntityT")
ModelT = TypeVar("ModelT", bound=Base)


class _Mapper(Generic[EntityT, ModelT]):
    """dataclass 엔티티 ↔ ORM 모델 범용 변환기(같은 이름의 필드를 복사)."""

    def __init__(
        self, entity_cls: type[EntityT], model_cls: type[ModelT], exclude: frozenset[str] = frozenset()
    ) -> None:
        self.entity_cls = entity_cls
        self.model_cls = model_cls
        self.fields = [
            f.name
            for f in dataclasses.fields(entity_cls)  # type: ignore[arg-type]
            if f.name not in exclude
        ]

    def to_entity(self, row: ModelT) -> EntityT:
        return self.entity_cls(**{name: getattr(row, name) for name in self.fields})

    def to_model(self, entity: EntityT) -> ModelT:
        return self.model_cls(**{name: getattr(entity, name) for name in self.fields})

    def copy_into(self, entity: EntityT, row: ModelT) -> None:
        for name in self.fields:
            setattr(row, name, getattr(entity, name))


class _SqlRepo:
    """세션을 보관하는 공통 부모."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def _save(self, mapper: _Mapper[Any, Any], entity: Any) -> None:
        row = self._s.get(mapper.model_cls, entity.id)
        if row is None:
            raise LookupError(f"{mapper.model_cls.__tablename__}:{entity.id} 이(가) 없습니다.")
        mapper.copy_into(entity, row)
        self._s.flush()


# ---------------------------------------------------------------------------
_user = _Mapper(dom.User, orm.UserModel)
_consent = _Mapper(dom.UserConsent, orm.UserConsentModel)
_session = _Mapper(dom.CheckSession, orm.CheckSessionModel)
_document = _Mapper(dom.Document, orm.DocumentModel)
_fact = _Mapper(dom.ExtractedFact, orm.ExtractedFactModel)
_event = _Mapper(dom.CaseEvent, orm.CaseEventModel)
_evidence = _Mapper(dom.Evidence, orm.EvidenceModel)
_audit = _Mapper(dom.AuditLog, orm.AuditLogModel)
_report = _Mapper(dom.InspectionReport, orm.InspectionReportModel, exclude=frozenset({"items"}))
_CASE_JSON = frozenset({"unverified_items", "consultation_prep"})
_case = _Mapper(dom.Case, orm.CaseModel, exclude=_CASE_JSON)


# ---------------------------------------------------------------------------
# JSON 컬럼 직렬화 (도메인 객체 ↔ dict). 날짜·UUID 는 ISO 문자열로 저장한다.
# ---------------------------------------------------------------------------
def _iso(v: date | datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _uuid(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


def _citation_to_json(c: dom.ReportCitation) -> dict[str, Any]:
    return {
        "requested_chunk_ref": c.requested_chunk_ref,
        "chunk_id": c.chunk_id,
        "quoted_text": c.quoted_text,
        "check_result": c.check_result,
        "source_title": c.source_title,
        "source_url": c.source_url,
        "article_ref": c.article_ref,
        "effective_date": _iso(c.effective_date),
    }


def _citation_from_json(d: dict[str, Any]) -> dom.ReportCitation:
    eff = d.get("effective_date")
    return dom.ReportCitation(
        requested_chunk_ref=d["requested_chunk_ref"],
        chunk_id=d.get("chunk_id"),
        quoted_text=d["quoted_text"],
        check_result=CitationCheck(d["check_result"]),
        source_title=d.get("source_title"),
        source_url=d.get("source_url"),
        article_ref=d.get("article_ref"),
        effective_date=date.fromisoformat(eff) if eff else None,
    )


def _item_to_json(i: dom.ReportItem) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "category": i.category,
        "content": i.content,
        "sort_order": i.sort_order,
        "fact_id": str(i.fact_id) if i.fact_id else None,
        "hold_reason": i.hold_reason,
        "citations": [_citation_to_json(c) for c in i.citations],
    }


def _item_from_json(d: dict[str, Any]) -> dom.ReportItem:
    return dom.ReportItem(
        id=uuid.UUID(d["id"]),
        category=ReportCategory(d["category"]),
        content=d["content"],
        sort_order=int(d["sort_order"]),
        fact_id=_uuid(d.get("fact_id")),
        hold_reason=HoldReason(d["hold_reason"]) if d.get("hold_reason") else None,
        citations=[_citation_from_json(c) for c in d.get("citations", [])],
    )


def _unverified_to_json(u: dom.UnverifiedItem) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "content": u.content,
        "status": u.status,
        "created_at": _iso(u.created_at),
        "resolved_at": _iso(u.resolved_at),
    }


def _unverified_from_json(d: dict[str, Any]) -> dom.UnverifiedItem:
    return dom.UnverifiedItem(
        id=uuid.UUID(d["id"]),
        content=d["content"],
        status=UnverifiedStatus(d["status"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        resolved_at=datetime.fromisoformat(d["resolved_at"]) if d.get("resolved_at") else None,
    )


def _prep_to_json(p: dom.ConsultationPrep) -> dict[str, Any]:
    return {
        "version": p.version,
        "status": p.status,
        "agency_code": p.agency_code,
        "agency_name": p.agency_name,
        "summary_text": p.summary_text,
        "checklist": p.checklist,
        "questions": p.questions,
        "created_at": _iso(p.created_at),
        "approved_at": _iso(p.approved_at),
    }


def _prep_from_json(d: dict[str, Any]) -> dom.ConsultationPrep:
    return dom.ConsultationPrep(
        version=int(d["version"]),
        status=PrepStatus(d["status"]),
        agency_code=d.get("agency_code"),
        agency_name=d.get("agency_name"),
        summary_text=d["summary_text"],
        checklist=list(d["checklist"]),
        questions=list(d["questions"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        approved_at=datetime.fromisoformat(d["approved_at"]) if d.get("approved_at") else None,
    )


def _case_to_entity(row: orm.CaseModel) -> dom.Case:
    case = _case.to_entity(row)
    case.unverified_items = [_unverified_from_json(u) for u in (row.unverified_items or [])]
    case.consultation_prep = _prep_from_json(row.consultation_prep) if row.consultation_prep else None
    return case


def _case_json_into(case: dom.Case, row: orm.CaseModel) -> None:
    row.unverified_items = [_unverified_to_json(u) for u in case.unverified_items]
    row.consultation_prep = _prep_to_json(case.consultation_prep) if case.consultation_prep else None


# ---------------------------------------------------------------------------
class SqlUserRepository(_SqlRepo, ports.UserRepository):
    def get(self, user_id: uuid.UUID) -> dom.User | None:
        row = self._s.get(orm.UserModel, user_id)
        return _user.to_entity(row) if row else None

    def find_by_email(self, email: str) -> dom.User | None:
        row = self._s.scalars(select(orm.UserModel).where(orm.UserModel.email == email).limit(1)).first()
        return _user.to_entity(row) if row else None

    def add(self, user: dom.User) -> None:
        try:
            self._s.add(_user.to_model(user))
            self._s.flush()
        except IntegrityError as exc:  # 같은 이메일 동시 가입(UNIQUE)
            raise DuplicateError("이미 가입된 이메일입니다.") from exc

    def save(self, user: dom.User) -> None:
        self._save(_user, user)


class SqlConsentRepository(_SqlRepo, ports.ConsentRepository):
    def add(self, consent: dom.UserConsent) -> None:
        self._s.add(_consent.to_model(consent))
        self._s.flush()

    def latest(self, user_id: uuid.UUID, consent_type: ConsentType) -> dom.UserConsent | None:
        row = self._s.scalars(
            select(orm.UserConsentModel)
            .where(orm.UserConsentModel.user_id == user_id, orm.UserConsentModel.consent_type == consent_type)
            .order_by(orm.UserConsentModel.agreed_at.desc())
            .limit(1)
        ).first()
        return _consent.to_entity(row) if row else None


class SqlCheckSessionRepository(_SqlRepo, ports.CheckSessionRepository):
    def add(self, session: dom.CheckSession) -> None:
        self._s.add(_session.to_model(session))
        self._s.flush()

    def get(self, session_id: uuid.UUID) -> dom.CheckSession | None:
        row = self._s.get(orm.CheckSessionModel, session_id)
        return _session.to_entity(row) if row else None

    def save(self, session: dom.CheckSession) -> None:
        self._save(_session, session)

    def find_by_thread(self, thread_id: str) -> dom.CheckSession | None:
        row = self._s.scalars(
            select(orm.CheckSessionModel).where(orm.CheckSessionModel.thread_id == thread_id).limit(1)
        ).first()
        return _session.to_entity(row) if row else None

    def soft_delete_by_owner(self, user_id: uuid.UUID, now: datetime) -> int:
        m = orm.CheckSessionModel
        result = self._s.execute(
            update(m)
            .where(m.user_id == user_id, m.deleted_at.is_(None))
            .values(deleted_at=now, updated_at=now)
        )
        self._s.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


class SqlDocumentRepository(_SqlRepo, ports.DocumentRepository):
    def add(self, document: dom.Document) -> None:
        self._s.add(_document.to_model(document))
        self._s.flush()

    def get(self, document_id: uuid.UUID) -> dom.Document | None:
        row = self._s.get(orm.DocumentModel, document_id)
        return _document.to_entity(row) if row else None

    def list_by_session(self, session_id: uuid.UUID) -> list[dom.Document]:
        rows = self._s.scalars(
            select(orm.DocumentModel).where(
                orm.DocumentModel.check_session_id == session_id, orm.DocumentModel.deleted_at.is_(None)
            )
        )
        return [_document.to_entity(r) for r in rows]

    def save(self, document: dom.Document) -> None:
        self._save(_document, document)


class SqlFactRepository(_SqlRepo, ports.FactRepository):
    def add_many(self, facts: list[dom.ExtractedFact]) -> None:
        self._s.add_all([_fact.to_model(f) for f in facts])
        self._s.flush()

    def get(self, fact_id: uuid.UUID) -> dom.ExtractedFact | None:
        row = self._s.get(orm.ExtractedFactModel, fact_id)
        return _fact.to_entity(row) if row else None

    def list_by_session(self, session_id: uuid.UUID) -> list[dom.ExtractedFact]:
        rows = self._s.scalars(
            select(orm.ExtractedFactModel)
            .where(orm.ExtractedFactModel.check_session_id == session_id)
            .order_by(orm.ExtractedFactModel.created_at)
        )
        return [_fact.to_entity(r) for r in rows]

    def save(self, fact: dom.ExtractedFact) -> None:
        self._save(_fact, fact)


class SqlReportRepository(_SqlRepo, ports.ReportRepository):
    def add(self, report: dom.InspectionReport) -> None:
        row = _report.to_model(report)
        row.items = [_item_to_json(i) for i in report.items]  # 항목·인용을 한 행에 함께 저장
        self._s.add(row)
        self._s.flush()

    def get(self, report_id: uuid.UUID) -> dom.InspectionReport | None:
        row = self._s.get(orm.InspectionReportModel, report_id)
        return self._load(row) if row else None

    def save_status(self, report: dom.InspectionReport) -> None:
        row = self._s.get(orm.InspectionReportModel, report.id)
        if row is None:
            raise LookupError(f"inspection_reports:{report.id} 이(가) 없습니다.")
        row.status = report.status
        row.approved_at = report.approved_at
        self._s.flush()

    def latest_approved(self, session_id: uuid.UUID) -> dom.InspectionReport | None:
        row = self._s.scalars(
            select(orm.InspectionReportModel)
            .where(
                orm.InspectionReportModel.check_session_id == session_id,
                orm.InspectionReportModel.status == ReportStatus.APPROVED,
            )
            .order_by(orm.InspectionReportModel.version.desc())
            .limit(1)
        ).first()
        return self._load(row) if row else None

    def latest_version(self, session_id: uuid.UUID) -> int:
        value = self._s.scalar(
            select(func.max(orm.InspectionReportModel.version)).where(
                orm.InspectionReportModel.check_session_id == session_id
            )
        )
        return int(value or 0)

    def supersede_previous(self, session_id: uuid.UUID) -> None:
        self._s.execute(
            update(orm.InspectionReportModel)
            .where(
                orm.InspectionReportModel.check_session_id == session_id,
                orm.InspectionReportModel.status == ReportStatus.APPROVED,
            )
            .values(status=ReportStatus.SUPERSEDED)
        )
        self._s.flush()

    @staticmethod
    def _load(row: orm.InspectionReportModel) -> dom.InspectionReport:
        report = _report.to_entity(row)
        report.items = sorted((_item_from_json(d) for d in row.items), key=lambda i: i.sort_order)
        return report


class SqlCaseRepository(_SqlRepo, ports.CaseRepository):
    def add(self, case: dom.Case) -> None:
        try:
            row = _case.to_model(case)
            _case_json_into(case, row)
            self._s.add(row)
            self._s.flush()
        except IntegrityError as exc:
            # origin_session_id UNIQUE 위반 = 같은 점검의 동시 전환 → 도메인 예외로 번역
            if "origin_session_id" in str(exc.orig):
                raise DuplicateError("이미 사건으로 전환된 점검입니다.") from exc
            raise

    def get(self, case_id: uuid.UUID) -> dom.Case | None:
        row = self._s.get(orm.CaseModel, case_id)
        return _case_to_entity(row) if row else None

    def save(self, case: dom.Case) -> None:
        row = self._s.get(orm.CaseModel, case.id)
        if row is None:
            raise LookupError(f"cases:{case.id} 이(가) 없습니다.")
        _case.copy_into(case, row)
        _case_json_into(case, row)
        self._s.flush()

    def find_by_origin_session(self, session_id: uuid.UUID) -> dom.Case | None:
        row = self._s.scalars(
            select(orm.CaseModel).where(orm.CaseModel.origin_session_id == session_id).limit(1)
        ).first()
        return _case_to_entity(row) if row else None

    def find_by_thread(self, thread_id: str) -> dom.Case | None:
        row = self._s.scalars(
            select(orm.CaseModel).where(orm.CaseModel.thread_id == thread_id).limit(1)
        ).first()
        return _case_to_entity(row) if row else None

    def soft_delete_by_owner(self, owner_id: uuid.UUID, now: datetime) -> int:
        m = orm.CaseModel
        result = self._s.execute(
            update(m)
            .where(m.owner_id == owner_id, m.deleted_at.is_(None))
            .values(deleted_at=now, updated_at=now)
        )
        self._s.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def list_by_owner(self, owner_id: uuid.UUID) -> list[dom.Case]:
        rows = self._s.scalars(
            select(orm.CaseModel)
            .where(orm.CaseModel.owner_id == owner_id, orm.CaseModel.deleted_at.is_(None))
            .order_by(orm.CaseModel.updated_at.desc())
        )
        return [_case_to_entity(r) for r in rows]


class SqlCaseRecordRepository(_SqlRepo, ports.CaseRecordRepository):
    def add_event(self, event: dom.CaseEvent) -> None:
        self._s.add(_event.to_model(event))
        self._s.flush()

    def list_events(self, case_id: uuid.UUID) -> list[dom.CaseEvent]:
        rows = self._s.scalars(select(orm.CaseEventModel).where(orm.CaseEventModel.case_id == case_id))
        return [_event.to_entity(r) for r in rows]

    def add_evidence(self, evidence: dom.Evidence) -> None:
        self._s.add(_evidence.to_model(evidence))
        self._s.flush()

    def list_evidences(self, case_id: uuid.UUID) -> list[dom.Evidence]:
        rows = self._s.scalars(
            select(orm.EvidenceModel)
            .where(orm.EvidenceModel.case_id == case_id)
            .order_by(orm.EvidenceModel.created_at)
        )
        return [_evidence.to_entity(r) for r in rows]


class SqlPurgeRepository(_SqlRepo, ports.PurgeRepository):
    """물리 파기. 하위 기록은 FK ON DELETE CASCADE 로 함께 지워진다."""

    def cases_deleted_before(self, cutoff: datetime) -> list[uuid.UUID]:
        return list(
            self._s.scalars(
                select(orm.CaseModel.id).where(
                    orm.CaseModel.deleted_at.is_not(None), orm.CaseModel.deleted_at < cutoff
                )
            )
        )

    def documents_of_case(self, case_id: uuid.UUID) -> list[dom.Document]:
        case = self._s.get(orm.CaseModel, case_id)
        cond = orm.DocumentModel.case_id == case_id
        if case is not None and case.origin_session_id is not None:
            cond = or_(cond, orm.DocumentModel.check_session_id == case.origin_session_id)
        return [_document.to_entity(r) for r in self._s.scalars(select(orm.DocumentModel).where(cond))]

    def thread_ids_of(self, case_id: uuid.UUID | None, session_id: uuid.UUID | None) -> list[str]:
        ids: list[str | None] = []
        if case_id is not None:
            ids.append(self._s.scalar(select(orm.CaseModel.thread_id).where(orm.CaseModel.id == case_id)))
        if session_id is not None:
            ids.append(
                self._s.scalar(
                    select(orm.CheckSessionModel.thread_id).where(orm.CheckSessionModel.id == session_id)
                )
            )
        return [i for i in ids if i]

    def hard_delete_case(self, case_id: uuid.UUID) -> None:
        case = self._s.get(orm.CaseModel, case_id)
        if case is None:
            return
        origin = case.origin_session_id
        # 사건 → (CASCADE) 타임라인·증거·사건 문서. 스냅샷·미확인·상담자료는 사건 행의 컬럼이라 함께 사라진다
        self._s.execute(delete(orm.CaseModel).where(orm.CaseModel.id == case_id))
        if origin is not None:
            # 원본 점검 → (CASCADE) 문서·추출 사실(확인 결과 포함)·보고서(항목·인용 포함)
            self._s.execute(delete(orm.CheckSessionModel).where(orm.CheckSessionModel.id == origin))
        self._s.flush()

    # --- v0.9 데이터별 보관 정책 -------------------------------------------
    def idle_unconverted_sessions(self, cutoff: datetime) -> list[uuid.UUID]:
        s, d, f, r = (
            orm.CheckSessionModel,
            orm.DocumentModel,
            orm.ExtractedFactModel,
            orm.InspectionReportModel,
        )
        # '마지막 이용' = 점검 자체 또는 하위 기록 중 가장 늦은 변경. 하나라도 cutoff 이후면 대상 아님
        recent = or_(
            exists().where(d.check_session_id == s.id, d.created_at >= cutoff),
            exists().where(f.check_session_id == s.id, f.updated_at >= cutoff),
            exists().where(r.check_session_id == s.id, or_(r.created_at >= cutoff, r.approved_at >= cutoff)),
        )
        converted = exists().where(orm.CaseModel.origin_session_id == s.id)
        return list(self._s.scalars(select(s.id).where(s.updated_at < cutoff, ~converted, ~recent)))

    def cases_closed_before(self, cutoff: datetime) -> list[uuid.UUID]:
        return list(
            self._s.scalars(
                select(orm.CaseModel.id).where(
                    orm.CaseModel.status == CaseStatus.CLOSED,
                    orm.CaseModel.closed_at.is_not(None),
                    orm.CaseModel.closed_at < cutoff,
                )
            )
        )

    def documents_of_session(self, session_id: uuid.UUID) -> list[dom.Document]:
        rows = self._s.scalars(
            select(orm.DocumentModel).where(orm.DocumentModel.check_session_id == session_id)
        )
        return [_document.to_entity(r) for r in rows]

    def hard_delete_session(self, session_id: uuid.UUID) -> None:
        self._s.execute(delete(orm.CheckSessionModel).where(orm.CheckSessionModel.id == session_id))
        self._s.flush()

    def withdrawn_users_with_data(self) -> list[uuid.UUID]:
        u = orm.UserModel
        has_data = or_(
            exists().where(orm.CheckSessionModel.user_id == u.id),
            exists().where(orm.CaseModel.owner_id == u.id),
            exists().where(orm.UserConsentModel.user_id == u.id),
            exists().where(orm.DocumentModel.owner_id == u.id),
        )
        return list(self._s.scalars(select(u.id).where(u.status == UserStatus.WITHDRAWN, has_data)))

    def case_ids_of_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return list(self._s.scalars(select(orm.CaseModel.id).where(orm.CaseModel.owner_id == user_id)))

    def session_ids_of_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self._s.scalars(select(orm.CheckSessionModel.id).where(orm.CheckSessionModel.user_id == user_id))
        )

    def delete_consents_of_user(self, user_id: uuid.UUID) -> int:
        result = self._s.execute(delete(orm.UserConsentModel).where(orm.UserConsentModel.user_id == user_id))
        self._s.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def delete_audit_logs_before(self, cutoff: datetime) -> int:
        result = self._s.execute(delete(orm.AuditLogModel).where(orm.AuditLogModel.created_at < cutoff))
        self._s.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def delete_withdrawn_users_before(self, cutoff: datetime) -> int:
        u, a = orm.UserModel, orm.AuditLogModel
        result = self._s.execute(
            delete(u).where(
                and_(
                    u.status == UserStatus.WITHDRAWN,
                    u.deleted_at.is_not(None),
                    u.deleted_at < cutoff,
                    ~exists().where(a.actor_id == u.id),
                    ~exists().where(orm.CheckSessionModel.user_id == u.id),
                    ~exists().where(orm.CaseModel.owner_id == u.id),
                    ~exists().where(orm.UserConsentModel.user_id == u.id),
                    ~exists().where(orm.ExtractedFactModel.confirmed_by == u.id),
                )
            )
        )
        self._s.flush()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


class SqlAuditLogRepository(_SqlRepo, ports.AuditLogRepository):
    def add(self, log: dom.AuditLog) -> None:
        self._s.add(_audit.to_model(log))
        self._s.flush()
