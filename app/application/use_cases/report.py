"""점검 보고서 유스케이스.

UC-06 보고서 초안 제출(2차 검증 결과 저장) / UC-07 보고서 승인
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.ports.services import Clock, KnowledgeBase, UnitOfWork
from app.application.use_cases.common import audit
from app.domain.entities import InspectionReport, ReportCitation, ReportItem, new_id
from app.domain.enums import AuditAction, CitationCheck, ReportCategory, ReportStatus, SessionStatus
from app.domain.errors import ConflictError, NotFoundError
from app.domain.policies import ChunkFacts, CitationVerifier, ReportCompositionPolicy


@dataclass(frozen=True)
class CitationInput:
    """AI 가 제시한 인용. '검증됨' 여부는 받지 않는다 — 서버가 판정한다(AI-03)."""

    source_chunk_id: str  # 벡터 지식 DB 의 청크 ID(업무 DB 와 FK 로 연결되지 않으므로 문자열)
    quoted_text: str


def _utc(value: datetime) -> datetime:
    """시간대 없는 값(SQLite 시험 DB)은 UTC 로 본다. 저장은 항상 UTC 다."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class ReportItemInput:
    category: ReportCategory
    content: str
    fact_id: uuid.UUID | None = None
    citations: tuple[CitationInput, ...] = ()


@dataclass(frozen=True)
class SubmitReportCommand:
    user_id: uuid.UUID
    session_id: uuid.UUID
    summary_text: str
    items: tuple[ReportItemInput, ...]
    # v0.11(선택): AI 가 초안을 만들 때 읽은 '사실 승인 시각'. 보내면 현재 값과 다를 때 저장을 거부한다
    # (초안을 만드는 동안 사용자가 사실을 고친 경우). 안 보내면 기존과 같이 동작한다.
    facts_confirmed_at: datetime | None = None


@dataclass(frozen=True)
class SubmitReportResult:
    report_id: uuid.UUID
    held_items: int  # 근거 미검증으로 보류된 항목 수(재검색 필요)


# ===========================================================================
# UC-06 보고서 초안 제출  ※ 호출 주체: LangGraph N4 VerifyEvidence → N5 ComposeReport
# ===========================================================================
class SubmitInspectionReport:
    """2차 검증 결과를 저장한다.

    1) 인용마다 서버가 청크 실재·출처 활성·시행일·인용문 일치를 판정(CitationVerifier)
    2) 법률 단정 표현이 있으면 저장 거부, '확인된 사실'은 승인 사실 연결 필수
    3) 검증 통과 인용이 없는 '주의' 항목은 보류(hold) — 가짜 출처는 확정 내용으로 나가지 않는다
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        policy: ReportCompositionPolicy,
        verifier: CitationVerifier,
        knowledge: KnowledgeBase,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._policy = policy
        self._verifier = verifier
        self._knowledge = knowledge

    def execute(self, cmd: SubmitReportCommand) -> SubmitReportResult:
        now = self._clock.now()
        with self._uow as uow:
            session = uow.sessions.get(cmd.session_id)
            if session is None:
                raise NotFoundError("점검 기록을 찾을 수 없습니다.")
            session.ensure_owned_by(cmd.user_id)
            if session.status not in (SessionStatus.FACTS_CONFIRMED, SessionStatus.REPORTED):
                raise ConflictError("사실 승인이 끝난 뒤에 보고서를 만들 수 있습니다.")

            if cmd.facts_confirmed_at is not None and (
                session.facts_confirmed_at is None
                or _utc(cmd.facts_confirmed_at) != _utc(session.facts_confirmed_at)
            ):
                raise ConflictError("초안을 만드는 동안 사실이 바뀌었습니다. 사실을 다시 읽고 작성해 주세요.")
            # v0.11 SUP-02: 요약문에는 인용을 붙일 자리가 없다.
            # 법률 단정 표현은 항목과 같이 저장 전에 거부한다.
            self._policy.validate_summary(cmd.summary_text)
            wanted = [c.source_chunk_id for i in cmd.items for c in i.citations]
            # 지식 DB 는 업무 DB 와 다른 저장소다(v0.6). 읽기 전용 조회라 트랜잭션에 묶을 필요가 없다.
            chunks = self._knowledge.chunk_facts(wanted) if wanted else {}
            report_id = new_id()
            items = [self._build_item(i, order, chunks, now) for order, i in enumerate(cmd.items)]
            items = self._policy.apply(items, uow.facts.list_by_session(session.id))
            self._policy.validate_summary_grounding(cmd.summary_text, items)  # v0.11 SUP-02

            report = InspectionReport(
                id=report_id,
                check_session_id=session.id,
                version=uow.reports.latest_version(session.id) + 1,
                status=ReportStatus.DRAFT,
                summary_text=cmd.summary_text.strip(),
                created_at=now,
                items=items,
            )
            uow.reports.add(report)
            uow.commit()
        return SubmitReportResult(report.id, sum(1 for i in items if i.is_held))

    def _build_item(
        self,
        src: ReportItemInput,
        order: int,
        chunks: dict[str, ChunkFacts],
        now: datetime,
    ) -> ReportItem:
        citations = []
        for c in src.citations:
            chunk = chunks.get(c.source_chunk_id)
            result = self._verifier.check(chunk, c.quoted_text, now.date())
            citations.append(
                ReportCitation(
                    requested_chunk_ref=c.source_chunk_id,  # AI 가 보낸 값 그대로(가짜 출처 추적)
                    # 지식 DB 에 없는 청크(가짜 출처)는 chunk_id 를 비워 '실재 근거'와 구분한다
                    chunk_id=None if result == CitationCheck.CHUNK_NOT_FOUND else c.source_chunk_id,
                    quoted_text=c.quoted_text,
                    check_result=result,
                    # 인용 당시 출처 정보 사본(FK 가 없으므로 보고서가 스스로 근거를 보존해야 한다)
                    source_title=chunk.source_title if chunk else None,
                    source_url=chunk.source_url if chunk else None,
                    article_ref=chunk.article_ref if chunk else None,
                    effective_date=chunk.effective_date if chunk else None,
                )
            )
        return ReportItem(
            id=new_id(),
            category=src.category,
            content=src.content.strip(),
            fact_id=src.fact_id,
            sort_order=order,
            citations=citations,
        )


# ===========================================================================
# UC-07 보고서 승인(사용자 최종 확인)
# ===========================================================================
@dataclass(frozen=True)
class ApproveReportCommand:
    user_id: uuid.UUID
    report_id: uuid.UUID


class ApproveInspectionReport:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: ApproveReportCommand) -> None:
        now = self._clock.now()
        with self._uow as uow:
            report = uow.reports.get(cmd.report_id)
            if report is None:
                raise NotFoundError("보고서를 찾을 수 없습니다.")
            session = uow.sessions.get(report.check_session_id)
            if session is None:
                raise NotFoundError("점검 기록을 찾을 수 없습니다.")
            session.ensure_owned_by(cmd.user_id)
            # v0.8 G4: 보고서 작성 뒤 사실을 다시 고쳤다면(확인 시각이 더 늦음) 그 보고서는 옛 사실 기준이다
            if session.facts_confirmed_at is not None and report.created_at < session.facts_confirmed_at:
                raise ConflictError("보고서 작성 뒤 사실이 바뀌었습니다. 보고서를 다시 작성해 주세요.")

            uow.reports.supersede_previous(session.id)  # 이전 승인본은 '대체됨'
            report.approve(now)
            uow.reports.save_status(report)
            session.mark_reported(now)
            uow.sessions.save(session)
            audit(
                uow,
                self._clock,
                cmd.user_id,
                AuditAction.UPDATE,
                "inspection_reports",
                report.id,
                {"status": ReportStatus.APPROVED, "version": report.version},
            )
            uow.commit()


# ===========================================================================
# UC-07b 보고서 상세 조회(v0.11) — 시민 본인만
# ===========================================================================
@dataclass(frozen=True)
class ReportDetail:
    report: InspectionReport
    is_stale: bool  # 초안 작성 뒤 사실이 바뀌어 승인할 수 없는 초안인지


class GetReportDetail:
    """보고서 1건(버전·상태·항목·인용 당시 출처·보류 사유)을 돌려준다. 열람은 감사 로그에 남긴다."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, user_id: uuid.UUID, report_id: uuid.UUID) -> ReportDetail:
        with self._uow as uow:
            report = uow.reports.get(report_id)
            if report is None:
                raise NotFoundError("보고서를 찾을 수 없습니다.")
            session = uow.sessions.get(report.check_session_id)
            if session is None:
                raise NotFoundError("보고서를 찾을 수 없습니다.")
            try:
                session.ensure_owned_by(user_id)
            except NotFoundError:
                # 남의 보고서·지워진 점검의 보고서는 '없는 보고서'와 같은 응답(존재 여부 은닉)
                raise NotFoundError("보고서를 찾을 수 없습니다.") from None
            stale = (
                report.status == ReportStatus.DRAFT
                and session.facts_confirmed_at is not None
                and report.created_at < session.facts_confirmed_at
            ) or (report.status == ReportStatus.DRAFT and session.status == SessionStatus.FACTS_EXTRACTED)
            audit(
                uow,
                self._clock,
                user_id,
                AuditAction.READ_SENSITIVE,
                "inspection_reports",
                report.id,
                {"version": report.version},
            )
            uow.commit()
        return ReportDetail(report, stale)
