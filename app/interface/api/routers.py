"""HTTP 라우터.

각 핸들러는 ① 요청 스키마 → 명령(Command) 변환 ② 유스케이스 실행 ③ 응답 변환만 한다.
업무 판단은 하지 않는다(얇은 컨트롤러, SRP).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from app.application.use_cases import account as uc_account
from app.application.use_cases import agent as uc_agent
from app.application.use_cases import case as uc_case
from app.application.use_cases import check_session as uc_check
from app.application.use_cases import report as uc_report
from app.container import Container
from app.interface.api import schemas
from app.interface.api.dependencies import (
    ServiceCaller,
    current_user_id,
    facts_writer,
    get_container,
    reports_writer,
    user_container,
)

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)

_MAX_UPLOAD = 10 * 1024 * 1024


async def _read_limited(file: UploadFile) -> bytes:
    """상한+1 바이트까지만 읽어 초과 여부를 판단한다 → 거대 파일로 메모리를 고갈시키는 공격 완화.

    점검 업로드와 사건 추가 첨부가 같이 쓴다(형식 검증은 유스케이스의 UploadPolicy 가 담당).
    """
    content = await file.read(_MAX_UPLOAD + 1)
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "파일이 너무 큽니다.")
    return content


# =============================================================================
# 계정(v0.9): 회원가입·로그인(이메일·비밀번호)·회원 탈퇴
# =============================================================================
@router.post("/auth/signup", response_model=schemas.IdOut, status_code=status.HTTP_201_CREATED, tags=["계정"])
def sign_up(body: schemas.SignUpIn, c: Container = Depends(get_container)) -> schemas.IdOut:
    """이메일·비밀번호로 가입. 이미 있는 이메일이면 409. 가입 후 /auth/login 으로 로그인한다."""
    cmd = uc_account.SignUpCommand(body.email, body.password, body.display_name)
    return schemas.IdOut(id=c.sign_up().execute(cmd))


@router.post("/auth/login", response_model=schemas.TokenOut, tags=["계정"])
def login(body: schemas.LoginIn, c: Container = Depends(get_container)) -> schemas.TokenOut:
    """로그인. 이메일이 없거나 비밀번호가 틀리면 같은 401. 연속 5회 실패하면 10분 잠금(429)."""
    user_id = c.login().execute(uc_account.LoginCommand(body.email, body.password))
    minutes = c.access_token_minutes
    now = c.clock.now()
    token = c.token_verifier.issue(user_id, now + timedelta(minutes=minutes), now)
    return schemas.TokenOut(access_token=token, expires_in=minutes * 60)


@router.post("/me/withdrawal", status_code=status.HTTP_204_NO_CONTENT, tags=["계정"])
def withdraw(
    body: schemas.WithdrawIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(get_container),
) -> None:
    """회원 탈퇴. 비밀번호를 다시 입력해야 한다(틀리면 403). 탈퇴 즉시 모든 토큰이 거부된다.

    ① 사용자 권한으로 탈퇴 확정(식별정보 삭제·점검·사건 논리 삭제) → ② 파기 전용 접속으로 즉시 물리 파기.
    ②가 실패해도 탈퇴는 유지되며, 정기 파기 배치가 남은 자료를 다시 지운다.
    """
    c.for_user(user_id).withdraw_account().execute(uc_account.WithdrawCommand(user_id, body.password))
    try:
        c.purge_user_data().execute(user_id)
    except Exception:  # 파기 실패는 배치가 재처리. 원인만 서버 로그에 남긴다(개인정보 없음)
        logger.exception("탈퇴 직후 파기 실패: 정기 배치에서 재처리")


# =============================================================================
# 동의·점검
# =============================================================================
@router.post("/consents", response_model=schemas.IdOut, status_code=status.HTTP_201_CREATED, tags=["동의"])
def record_consent(
    body: schemas.ConsentIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.IdOut:
    cmd = uc_check.RecordConsentCommand(user_id, body.consent_type, body.policy_version, body.agreed)
    return schemas.IdOut(id=c.record_consent().execute(cmd))


@router.post(
    "/check-sessions", response_model=schemas.IdOut, status_code=status.HTTP_201_CREATED, tags=["점검"]
)
def start_session(
    body: schemas.StartSessionIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.IdOut:
    cmd = uc_check.StartCheckSessionCommand(user_id, body.entry_type, body.situation_text)
    return schemas.IdOut(id=c.start_check_session().execute(cmd))


@router.post(
    "/check-sessions/{session_id}/documents",
    response_model=schemas.IdOut,
    status_code=status.HTTP_201_CREATED,
    tags=["점검"],
)
async def upload_document(
    session_id: uuid.UUID,
    file: UploadFile = File(...),
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.IdOut:
    content = await _read_limited(file)
    cmd = uc_check.UploadDocumentCommand(
        user_id, session_id, file.filename or "document.pdf", file.content_type or "", content
    )
    return schemas.IdOut(id=c.upload_document().execute(cmd))


@router.post(
    "/check-sessions/{session_id}/facts",
    response_model=schemas.FactIdsOut,
    status_code=status.HTTP_201_CREATED,
    tags=["점검(AI 내부)"],
)
def record_facts(
    session_id: uuid.UUID,
    body: schemas.FactsIn,
    caller: ServiceCaller = Depends(facts_writer),
) -> schemas.FactIdsOut:
    """AI 내부 API: 서비스 토큰(scope ai:facts:write)만 허용. 시민 토큰은 403(v0.9, R-01)."""
    cmd = uc_check.RecordExtractedFactsCommand(
        caller.user_id, session_id, tuple(uc_check.FactInput(**f.model_dump()) for f in body.facts)
    )
    return schemas.FactIdsOut(ids=caller.container.record_facts().execute(cmd))


@router.post("/facts/{fact_id}/confirmation", response_model=schemas.SessionStatusOut, tags=["점검"])
def confirm_fact(
    fact_id: uuid.UUID,
    body: schemas.ConfirmFactIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.SessionStatusOut:
    cmd = uc_check.ConfirmFactCommand(user_id, fact_id, body.action, body.new_value)
    return schemas.SessionStatusOut(session_status=c.confirm_fact().execute(cmd))


# =============================================================================
# 보고서
# =============================================================================
@router.post(
    "/check-sessions/{session_id}/reports",
    response_model=schemas.ReportOut,
    status_code=status.HTTP_201_CREATED,
    tags=["보고서(AI 내부)"],
)
def submit_report(
    session_id: uuid.UUID,
    body: schemas.ReportIn,
    caller: ServiceCaller = Depends(reports_writer),
) -> schemas.ReportOut:
    """AI 내부 API: 서비스 토큰(scope ai:reports:write)만 허용. 승인은 시민 API(/approval)."""
    items = tuple(
        uc_report.ReportItemInput(
            category=i.category,
            content=i.content,
            fact_id=i.fact_id,
            citations=tuple(uc_report.CitationInput(**ci.model_dump()) for ci in i.citations),
        )
        for i in body.items
    )
    cmd = uc_report.SubmitReportCommand(caller.user_id, session_id, body.summary_text, items)
    result = caller.container.submit_report().execute(cmd)
    return schemas.ReportOut(id=result.report_id, held_items=result.held_items)


@router.post("/reports/{report_id}/approval", status_code=status.HTTP_204_NO_CONTENT, tags=["보고서"])
def approve_report(
    report_id: uuid.UUID,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> None:
    c.approve_report().execute(uc_report.ApproveReportCommand(user_id, report_id))


# =============================================================================
# 사건
# =============================================================================
@router.post(
    "/check-sessions/{session_id}/case-conversion",
    response_model=schemas.ConvertOut,
    status_code=status.HTTP_201_CREATED,
    tags=["사건"],
)
def convert_to_case(
    session_id: uuid.UUID,
    body: schemas.ConvertIn,
    response: Response,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.ConvertOut:
    """멱등: 처음 전환하면 201, 이미 전환된 점검이면 같은 사건 ID 와 200 을 돌려준다."""
    cmd = uc_case.ConvertToCaseCommand(user_id, session_id, body.title, body.case_type)
    result = c.convert_to_case().execute(cmd)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return schemas.ConvertOut(id=result.case_id, created=result.created)


@router.post("/cases", response_model=schemas.IdOut, status_code=status.HTTP_201_CREATED, tags=["사건"])
def create_case(
    body: schemas.CaseIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.IdOut:
    cmd = uc_case.CreateCaseCommand(user_id, body.title, body.case_type, body.business_name)
    return schemas.IdOut(id=c.create_case().execute(cmd))


@router.get("/cases/{case_id}", response_model=schemas.CaseDetailOut, tags=["사건"])
def get_case(
    case_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), c: Container = Depends(user_container)
) -> schemas.CaseDetailOut:
    d = c.get_case_detail().execute(user_id, case_id)
    return schemas.CaseDetailOut(
        id=d.case.id,
        title=d.case.title,
        case_type=d.case.case_type,
        status=d.case.status,
        business_name=d.case.business_name,
        snapshot=d.snapshot,
        events=[
            schemas.EventOut(
                id=e.id, occurred_on=e.occurred_on, actor=e.actor, title=e.title, description=e.description
            )
            for e in d.events
        ],
        evidences=[
            schemas.EvidenceOut(id=e.id, evidence_type=e.evidence_type, title=e.title, status=e.status)
            for e in d.evidences
        ],
        unverified=[u.content for u in d.unverified],
    )


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["사건"])
def delete_case(
    case_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), c: Container = Depends(user_container)
) -> None:
    c.delete_case().execute(user_id, case_id)


@router.post(
    "/cases/{case_id}/events",
    response_model=schemas.IdOut,
    status_code=status.HTTP_201_CREATED,
    tags=["사건"],
)
def add_event(
    case_id: uuid.UUID,
    body: schemas.EventIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.IdOut:
    cmd = uc_case.AddCaseEventCommand(
        user_id, case_id, body.occurred_on, body.actor, body.title, body.description
    )
    return schemas.IdOut(id=c.add_case_event().execute(cmd))


@router.post(
    "/cases/{case_id}/documents",
    response_model=schemas.IdOut,
    status_code=status.HTTP_201_CREATED,
    tags=["사건"],
)
async def attach_case_document(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.IdOut:
    """사건에 PDF 추가 첨부(UC-18). 첨부 후 증거 등록에서 document_id 로 연결한다."""
    content = await _read_limited(file)
    cmd = uc_case.AttachCaseDocumentCommand(
        user_id, case_id, file.filename or "document.pdf", file.content_type or "", content
    )
    return schemas.IdOut(id=c.attach_case_document().execute(cmd))


@router.post(
    "/cases/{case_id}/evidences",
    response_model=schemas.IdOut,
    status_code=status.HTTP_201_CREATED,
    tags=["사건"],
)
def register_evidence(
    case_id: uuid.UUID,
    body: schemas.EvidenceIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.IdOut:
    cmd = uc_case.RegisterEvidenceCommand(user_id=user_id, case_id=case_id, **body.model_dump())
    return schemas.IdOut(id=c.register_evidence().execute(cmd))


@router.post(
    "/cases/{case_id}/consultation-preps",
    response_model=schemas.PrepOut,
    status_code=status.HTTP_201_CREATED,
    tags=["상담 준비"],
)
def prepare_consultation(
    case_id: uuid.UUID,
    body: schemas.PrepIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.PrepOut:
    cmd = uc_case.PrepareConsultationCommand(user_id, case_id, body.agency_code)
    return schemas.PrepOut(case_id=case_id, version=c.prepare_consultation().execute(cmd))


# =============================================================================
# AI 실행 스레드
# =============================================================================
@router.post(
    "/agent-threads", response_model=schemas.ThreadOut, status_code=status.HTTP_201_CREATED, tags=["AI 실행"]
)
def start_agent_thread(
    body: schemas.ThreadIn,
    user_id: uuid.UUID = Depends(current_user_id),
    c: Container = Depends(user_container),
) -> schemas.ThreadOut:
    cmd = uc_agent.StartThreadCommand(user_id, body.check_session_id, body.case_id)
    return schemas.ThreadOut(thread_id=c.start_agent_thread().execute(cmd))
