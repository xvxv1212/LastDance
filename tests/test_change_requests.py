"""DB·AI 수정 요청(DB-01~05, AI-02~04)의 '완료 기준'을 검증하는 테스트.

각 테스트 이름 앞의 코드가 수정 요청 ID 와 대응한다(보고서의 대응표 참고).
v0.6(DB 멘토링 피드백): 테이블을 컬럼·로그로 합친 뒤에도 각 요청의 '목적'이 지켜지는지 확인한다.
- DB-01 스냅샷: verified_snapshots 테이블 → cases.verified_facts 등 컬럼
- DB-05·AI-04 스레드: agent_threads → check_sessions/cases.thread_id
- AI-02 실행 기록: agent_runs → audit_logs(AI_CALL) + 애플리케이션 로그
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.application.use_cases.agent import ModelCallCommand
from app.domain import enums
from app.domain.enums import CitationCheck
from app.domain.errors import NotFoundError
from app.domain.policies import ChunkFacts, CitationVerifier
from app.infrastructure.db import models as orm
from tests.conftest import NOW, Ctx, agree_all

PDF = b"%PDF-1.4\n% synthetic pilates contract\n"


# ---------------------------------------------------------------------------
# 공통 시나리오 도우미
# ---------------------------------------------------------------------------
def _session(ctx: Ctx, user: uuid.UUID, consent: bool = True) -> str:
    if consent:
        agree_all(ctx, user)
    r = ctx.client.post(
        "/api/v1/check-sessions",
        headers=ctx.auth(user),
        json={"entry_type": "DISPUTE_OCCURRED", "situation_text": "필라테스 6개월권 환불 거절"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _upload(ctx: Ctx, user: uuid.UUID, sid: str) -> str:
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/documents",
        headers=ctx.auth(user),
        files={"file": ("계약서.pdf", PDF, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _approved(ctx: Ctx, user: uuid.UUID) -> tuple[str, str, str]:
    """상황 입력 → 문서 → 사실(원문·진술·미확인) → 승인 → 보고서 승인까지. (sid, report_id, doc_id)"""
    h = ctx.auth(user)
    sid = _session(ctx, user)
    doc = _upload(ctx, user, sid)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(user),
        json={
            "facts": [
                {
                    "fact_key": "PAYMENT_AMOUNT",
                    "value_text": "720,000원",
                    "source_kind": "DOCUMENT",
                    "document_id": doc,
                    "page_no": 2,
                    "offset_start": 233,
                    "offset_end": 262,
                    "value_normalized": {"amount": 720000},
                },
                {"fact_key": "PAYMENT_METHOD", "value_text": "카드 일시불", "source_kind": "STATEMENT"},
                {"fact_key": "CANCEL_REQUEST_DATE", "source_kind": "UNKNOWN"},
            ]
        },
    )
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    for fid, body in zip(
        ids,
        [{"action": "CONFIRM"}, {"action": "CONFIRM"}, {"action": "MODIFY", "new_value": "2026-05-10"}],
        strict=True,
    ):
        assert ctx.client.post(f"/api/v1/facts/{fid}/confirmation", headers=h, json=body).status_code == 200
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(user),
        json={
            "summary_text": "결제·계약 사실 확인, 환급 기준 비교 필요",
            "items": [
                {"category": "CONFIRMED_FACT", "content": "결제 금액 720,000원", "fact_id": ids[0]},
                {
                    "category": "CAUTION",
                    "content": "환급 기준과 계약 조항 비교가 필요합니다.",
                    "citations": [{"source_chunk_id": str(ctx.chunk_id), "quoted_text": "환급 기준"}],
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    report_id = str(r.json()["id"])
    assert ctx.client.post(f"/api/v1/reports/{report_id}/approval", headers=h).status_code == 204
    return sid, report_id, doc


def _convert(ctx: Ctx, user: uuid.UUID, sid: str) -> str:
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/case-conversion",
        headers=ctx.auth(user),
        json={"title": "필라테스 환불"},
    )
    assert r.status_code in (200, 201), r.text
    return str(r.json()["id"])


def _count(ctx: Ctx, model: type, **where: object) -> int:
    with ctx.factory() as s:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return int(s.scalar(q) or 0)


# ---------------------------------------------------------------------------
# DB-01 Snapshot 생성 시점과 FK
# ---------------------------------------------------------------------------
def test_db01_reported_but_not_converted_has_no_snapshot(ctx: Ctx) -> None:
    """'점검 완료·사건 미전환' 상태가 저장·조회되고, 존재하지 않는 case_id 로 Snapshot 을 만들지 않는다."""
    sid, report_id, _ = _approved(ctx, ctx.user_a)
    with ctx.factory() as s:
        sess = s.get(orm.CheckSessionModel, uuid.UUID(sid))
        assert sess is not None and sess.status == "REPORTED" and sess.facts_confirmed_at is not None
        approved = s.scalars(
            select(orm.ExtractedFactModel).where(
                orm.ExtractedFactModel.check_session_id == uuid.UUID(sid),
                orm.ExtractedFactModel.status.in_(["CONFIRMED", "MODIFIED"]),
            )
        ).all()
        assert len(approved) == 3  # 사건 전 '승인된 사실'은 확인 상태 + 확인 컬럼으로 조회
        assert all(f.confirmed_by == ctx.user_a and f.confirmed_at is not None for f in approved)
    assert _count(ctx, orm.CaseModel) == 0  # 스냅샷은 사건 행의 컬럼 → 사건 전에는 존재할 수 없다

    case_id = _convert(ctx, ctx.user_a, sid)
    with ctx.factory() as s:
        case = s.get(orm.CaseModel, uuid.UUID(case_id))
        assert case is not None and case.snapshot_version == 1 and case.snapshot_approved_at is not None
        assert str(case.snapshot_report_id) == report_id
        assert case.verified_facts is not None
        assert {f["key"] for f in case.verified_facts["facts"]} == {
            "PAYMENT_AMOUNT",
            "PAYMENT_METHOD",
            "CANCEL_REQUEST_DATE",
        }


def test_db01_snapshot_columns_must_be_complete(ctx: Ctx) -> None:
    """사실 묶음만 있고 버전·승인일시가 없는 '반쪽 스냅샷'은 DB 가 거부한다(ck_cases_snapshot_complete)."""
    with ctx.factory() as s:
        s.add(
            orm.CaseModel(
                id=uuid.uuid4(),
                owner_id=ctx.user_a,
                title="반쪽 스냅샷",
                case_type="OTHER_CONSUMER",
                verified_facts={"facts": []},
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


# ---------------------------------------------------------------------------
# DB-02 점검↔사건 1:1 · 중복 전환 멱등
# ---------------------------------------------------------------------------
def test_db02_double_conversion_keeps_one_case(ctx: Ctx) -> None:
    sid, _, _ = _approved(ctx, ctx.user_a)
    first, second = _convert(ctx, ctx.user_a, sid), _convert(ctx, ctx.user_a, sid)
    assert first == second
    assert _count(ctx, orm.CaseModel, origin_session_id=uuid.UUID(sid)) == 1
    # DB 수준에서도 같은 점검으로 두 번째 사건을 만들 수 없다(UNIQUE)
    with ctx.factory() as s:
        s.add(
            orm.CaseModel(
                id=uuid.uuid4(),
                owner_id=ctx.user_a,
                title="중복",
                case_type="FITNESS_REFUND",
                origin_session_id=uuid.UUID(sid),
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_db02_new_cases_without_origin_are_allowed(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    for title in ("신규 사건 1", "신규 사건 2"):
        r = ctx.client.post("/api/v1/cases", headers=h, json={"title": title, "case_type": "OTHER_CONSUMER"})
        assert r.status_code == 201
    assert _count(ctx, orm.CaseModel, origin_session_id=None) == 2


def test_db02_origin_session_must_have_same_owner(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    with ctx.factory() as s:
        s.add(
            orm.CaseModel(
                id=uuid.uuid4(),
                owner_id=ctx.user_b,
                title="남의 점검",
                case_type="FITNESS_REFUND",
                origin_session_id=uuid.UUID(sid),
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


# ---------------------------------------------------------------------------
# DB-03 소유자 권한과 부모 관계 무결성
# ---------------------------------------------------------------------------
def test_db03_db_rejects_cross_owner_and_bad_parents(ctx: Ctx) -> None:
    sid_a = uuid.UUID(_session(ctx, ctx.user_a))
    case_a, case_a2 = uuid.uuid4(), uuid.uuid4()
    with ctx.factory() as s:
        for cid in (case_a, case_a2):
            s.add(orm.CaseModel(id=cid, owner_id=ctx.user_a, title="A 사건", case_type="OTHER_CONSUMER"))
        s.commit()

    def rejected(row: object) -> None:
        with ctx.factory() as s:
            s.add(row)
            with pytest.raises(IntegrityError):
                s.commit()

    doc_kw = {
        "original_name": "x.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "sha256": "0" * 64,
        "retention_until": date(2027, 1, 1),
    }
    # B 소유 문서를 A 사건에 연결 → 복합 FK 위반
    rejected(
        orm.DocumentModel(
            id=uuid.uuid4(), owner_id=ctx.user_b, case_id=case_a, storage_key=uuid.uuid4().hex, **doc_kw
        )
    )
    # 부모 없는 문서 → CHECK 위반
    rejected(orm.DocumentModel(id=uuid.uuid4(), owner_id=ctx.user_a, storage_key=uuid.uuid4().hex, **doc_kw))
    # 사실 확인: '확인됨' 상태인데 확인자·일시가 없으면 거부(ck_extracted_facts_confirmation_complete)
    rejected(
        orm.ExtractedFactModel(
            id=uuid.uuid4(),
            check_session_id=sid_a,
            fact_key="BUSINESS_NAME",
            value_text="OO필라테스",
            source_kind="STATEMENT",
            status="CONFIRMED",
        )
    )
    # 증거: 다른 사건(같은 사용자)의 문서 연결 → '같은 사건' 복합 FK 위반
    doc_on_a = uuid.uuid4()
    with ctx.factory() as s:
        s.add(
            orm.DocumentModel(
                id=doc_on_a, owner_id=ctx.user_a, case_id=case_a, storage_key=uuid.uuid4().hex, **doc_kw
            )
        )
        s.commit()
    rejected(
        orm.EvidenceModel(
            id=uuid.uuid4(),
            case_id=case_a2,
            document_id=doc_on_a,
            evidence_type="CONTRACT",
            title="계약서",
            status="HELD",
        )
    )
    # 스레드: 같은 thread_id 를 두 사건이 함께 쓸 수 없다(UNIQUE) → 스레드는 부모 하나에만 속한다
    with ctx.factory() as s:
        row = s.get(orm.CaseModel, case_a)
        assert row is not None
        row.thread_id = "th-x"
        s.commit()
    with ctx.factory() as s:
        row = s.get(orm.CaseModel, case_a2)
        assert row is not None
        row.thread_id = "th-x"
        with pytest.raises(IntegrityError):
            s.commit()


def test_db03_api_cannot_link_other_users_document(ctx: Ctx) -> None:
    sid_b = _session(ctx, ctx.user_b)
    doc_b = _upload(ctx, ctx.user_b, sid_b)
    h = ctx.auth(ctx.user_a)
    case_a = ctx.client.post(
        "/api/v1/cases", headers=h, json={"title": "A 사건", "case_type": "OTHER_CONSUMER"}
    ).json()["id"]
    r = ctx.client.post(
        f"/api/v1/cases/{case_a}/evidences",
        headers=h,
        json={"evidence_type": "CONTRACT", "title": "남의 문서", "status": "HELD", "document_id": doc_b},
    )
    assert r.status_code == 404  # 존재 여부도 노출하지 않음


def test_db03_document_of_another_case_is_rejected(ctx: Ctx) -> None:
    sid, _, doc = _approved(ctx, ctx.user_a)
    _convert(ctx, ctx.user_a, sid)  # 문서는 전환된 사건에 귀속
    h = ctx.auth(ctx.user_a)
    other = ctx.client.post(
        "/api/v1/cases", headers=h, json={"title": "다른 사건", "case_type": "OTHER_CONSUMER"}
    ).json()["id"]
    r = ctx.client.post(
        f"/api/v1/cases/{other}/evidences",
        headers=h,
        json={"evidence_type": "CONTRACT", "title": "계약서", "status": "HELD", "document_id": doc},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 빅데이터 분석 정의서: 근거 유형(미확인)·원문 오프셋
# ---------------------------------------------------------------------------
def test_unknown_fact_rules(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    sid = _session(ctx, ctx.user_a)
    bad = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {"fact_key": "CANCEL_REQUEST_DATE", "value_text": "아마 5월쯤", "source_kind": "UNKNOWN"}
            ]
        },
    )
    assert bad.status_code == 422  # 미확인에 추정값 금지
    bad_offset = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {
                    "fact_key": "PAYMENT_METHOD",
                    "value_text": "카드",
                    "source_kind": "STATEMENT",
                    "offset_start": 9,
                    "offset_end": 3,
                }
            ]
        },
    )
    assert bad_offset.status_code == 422
    fid = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={"facts": [{"fact_key": "CANCEL_REQUEST_DATE", "source_kind": "UNKNOWN"}]},
    ).json()["ids"][0]
    assert (
        ctx.client.post(
            f"/api/v1/facts/{fid}/confirmation", headers=h, json={"action": "CONFIRM"}
        ).status_code
        == 422
    )
    ok = ctx.client.post(
        f"/api/v1/facts/{fid}/confirmation", headers=h, json={"action": "MODIFY", "new_value": "2026-05-10"}
    )
    assert ok.status_code == 200
    with ctx.factory() as s:
        fact = s.get(orm.ExtractedFactModel, uuid.UUID(fid))
        assert fact is not None and fact.source_kind == "STATEMENT" and fact.value_text == "2026-05-10"
        assert fact.ai_value_text is None  # AI 원래 값(없음)이 그대로 남는다(AI-01)
        assert fact.status == "MODIFIED" and fact.confirmed_by == ctx.user_a
        log = s.scalars(
            select(orm.AuditLogModel).where(
                orm.AuditLogModel.target_id == fact.id, orm.AuditLogModel.action == "UPDATE"
            )
        ).one()
        assert log.detail == {"action": "MODIFY", "fact_key": "CANCEL_REQUEST_DATE"}  # 값 원문은 없음


# ---------------------------------------------------------------------------
# AI-03 인용 검증
# ---------------------------------------------------------------------------
def test_ai03_citation_verifier_outcomes() -> None:
    v, today = CitationVerifier(), date(2026, 11, 4)
    body = "체육시설업 이용 중 해지 시 환급 기준"
    assert v.check(None, "환급", today) == CitationCheck.CHUNK_NOT_FOUND
    assert v.check(ChunkFacts(body, False, None), "환급", today) == CitationCheck.SOURCE_INACTIVE
    assert v.check(ChunkFacts(body, True, date(2027, 1, 1)), "환급", today) == CitationCheck.NOT_YET_EFFECTIVE
    assert v.check(ChunkFacts(body, True, None), "전액 환불", today) == CitationCheck.QUOTE_MISMATCH
    assert v.check(ChunkFacts(body, True, date(2025, 1, 1)), "해지 시  환급", today) == CitationCheck.VERIFIED


def test_ai03_mismatched_quote_is_held_not_confirmed(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    sid = _session(ctx, ctx.user_a)
    fid = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [{"fact_key": "BUSINESS_NAME", "value_text": "OO필라테스", "source_kind": "STATEMENT"}]
        },
    ).json()["ids"][0]
    ctx.client.post(f"/api/v1/facts/{fid}/confirmation", headers=h, json={"action": "CONFIRM"})
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "요약",
            "items": [
                {
                    "category": "CAUTION",
                    "content": "환급 기준 확인 필요",
                    "citations": [
                        {"source_chunk_id": str(ctx.chunk_id), "quoted_text": "전액 환불해야 한다"}
                    ],
                }
            ],
        },
    )
    assert r.status_code == 201 and r.json()["held_items"] == 1
    with ctx.factory() as s:
        report = s.scalars(select(orm.InspectionReportModel)).one()
        (item,) = report.items  # 항목·인용은 보고서 행의 JSON 컬럼
        (cit,) = item["citations"]
        assert cit["check_result"] == "QUOTE_MISMATCH" and item["hold_reason"] == "CITATION_FAILED"
        assert cit["chunk_id"] == ctx.chunk_id
        # 인용 당시 출처 정보 사본: 지식 DB 가 바뀌어도 보고서는 무엇을 근거로 봤는지 보존
        assert cit["source_url"] == "https://www.law.go.kr/" and cit["effective_date"] == "2025-01-01"


def test_ai03_fake_chunk_keeps_request_but_no_chunk_id(ctx: Ctx) -> None:
    """지식 DB 에 없는 청크(가짜 출처)는 chunk_id 를 비우고, AI 가 보낸 참조는 감사용으로 남긴다."""
    h = ctx.auth(ctx.user_a)
    sid = _session(ctx, ctx.user_a)
    fid = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [{"fact_key": "BUSINESS_NAME", "value_text": "OO필라테스", "source_kind": "STATEMENT"}]
        },
    ).json()["ids"][0]
    ctx.client.post(f"/api/v1/facts/{fid}/confirmation", headers=h, json={"action": "CONFIRM"})
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "요약",
            "items": [
                {
                    "category": "CAUTION",
                    "content": "환급 기준 확인 필요",
                    "citations": [{"source_chunk_id": "made-up#9", "quoted_text": "환급 기준"}],
                }
            ],
        },
    )
    assert r.status_code == 201 and r.json()["held_items"] == 1
    with ctx.factory() as s:
        (cit,) = s.scalars(select(orm.InspectionReportModel)).one().items[0]["citations"]
    assert cit["check_result"] == "CHUNK_NOT_FOUND" and cit["chunk_id"] is None
    assert cit["requested_chunk_ref"] == "made-up#9" and cit["source_url"] is None


# ---------------------------------------------------------------------------
# AI-02 외부 모델 호출 전 InputGuard (fail-closed)
# ---------------------------------------------------------------------------
def _thread(ctx: Ctx, user: uuid.UUID, sid: str) -> str:
    r = ctx.client.post("/api/v1/agent-threads", headers=ctx.auth(user), json={"check_session_id": sid})
    assert r.status_code == 201, r.text
    return str(r.json()["thread_id"])


def _overseas(ctx: Ctx, user: uuid.UUID, agreed: bool) -> None:
    r = ctx.client.post(
        "/api/v1/consents",
        headers=ctx.auth(user),
        json={"consent_type": "OVERSEAS_TRANSFER", "policy_version": "2026-11-v1", "agreed": agreed},
    )
    assert r.status_code == 201


def _call(ctx: Ctx, user: uuid.UUID, thread: str, body: str):  # type: ignore[no-untyped-def]
    return (
        ctx.container.for_user(user)
        .invoke_external_model()
        .execute(ModelCallCommand(user, thread, "N1 ExtractFacts", body))
    )


def test_ai02_no_basis_means_zero_external_calls(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    th = _thread(ctx, ctx.user_a, sid)
    out = _call(ctx, ctx.user_a, th, "3월에 6개월 등록했어요")
    assert not out.allowed and out.blocked_code == enums.GuardCode.NO_TRANSFER_BASIS
    assert ctx.model.calls == []
    with ctx.factory() as s:
        log = s.scalars(select(orm.AuditLogModel).where(orm.AuditLogModel.action == "AI_CALL")).one()
        assert log.id == out.audit_id and str(log.target_id) == sid and log.target_type == "check_sessions"
        assert log.detail == {
            "node": "N1 ExtractFacts",
            "status": "BLOCKED",
            "external_api": False,
            "transfer_basis": "NOT_APPLICABLE",
            "code": "NO_TRANSFER_BASIS",
        }


def test_ai02_withdrawn_consent_blocks(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    th = _thread(ctx, ctx.user_a, sid)
    _overseas(ctx, ctx.user_a, True)
    _overseas(ctx, ctx.user_a, False)  # 철회
    out = _call(ctx, ctx.user_a, th, "상황 설명")
    assert out.blocked_code == enums.GuardCode.CONSENT_WITHDRAWN and ctx.model.calls == []


def test_ai02_excessive_sensitive_data_blocks(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    th = _thread(ctx, ctx.user_a, sid)
    _overseas(ctx, ctx.user_a, True)
    phones = " ".join(f"010-1234-56{i:02d}" for i in range(4))
    out = _call(ctx, ctx.user_a, th, f"연락처 {phones}")
    assert out.blocked_code == enums.GuardCode.SENSITIVE_DATA and ctx.model.calls == []


def test_ai02_allowed_call_sends_masked_text_and_logs_no_raw_data(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    th = _thread(ctx, ctx.user_a, sid)
    _overseas(ctx, ctx.user_a, True)
    raw_phone = "010-9876-5432"
    out = _call(ctx, ctx.user_a, th, f"사업자 연락처 {raw_phone}, 위약금 30% 공제 통보")
    assert out.allowed and len(ctx.model.calls) == 1
    assert raw_phone not in ctx.model.calls[0] and "[휴대전화]" in ctx.model.calls[0]
    with ctx.factory() as s:
        log = s.scalars(select(orm.AuditLogModel).where(orm.AuditLogModel.action == "AI_CALL")).one()
        assert log.detail is not None
        assert log.detail["external_api"] is True and log.detail["transfer_basis"] == "CONSENT"
        assert log.detail["status"] == "SUCCEEDED" and log.detail["model"] == "fake-llm"
        dump = json.dumps(
            [[str(v) for v in row] for row in s.execute(text("SELECT * FROM audit_logs"))],
            ensure_ascii=False,
        )
    assert raw_phone not in dump  # 감사 로그에 원문 개인정보 없음


def test_ai02_metrics_go_to_app_log_without_raw_text(ctx: Ctx, caplog: pytest.LogCaptureFixture) -> None:
    """토큰·지연시간(구 agent_runs 의 성능 컬럼)은 애플리케이션 로그로 남고, 원문은 없다."""
    sid = _session(ctx, ctx.user_a)
    th = _thread(ctx, ctx.user_a, sid)
    _overseas(ctx, ctx.user_a, True)
    with caplog.at_level("INFO", logger="gwonri.ai.metrics"):
        _call(ctx, ctx.user_a, th, "사업자 연락처 010-9876-5432")
    (rec,) = [r for r in caplog.records if r.name == "gwonri.ai.metrics"]
    assert rec.__dict__["input_tokens"] == 10 and rec.__dict__["latency_ms"] >= 0
    assert "010-9876-5432" not in str(rec.__dict__)


# ---------------------------------------------------------------------------
# AI-04 / DB-05 스레드 소유권·추적성
# ---------------------------------------------------------------------------
def test_ai04_thread_cannot_be_reused_by_others_or_after_delete(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    th = _thread(ctx, ctx.user_a, sid)
    assert _thread(ctx, ctx.user_a, sid) == th  # 같은 점검이면 같은 스레드(멱등)
    with pytest.raises(NotFoundError):
        _call(ctx, ctx.user_b, th, "남의 스레드 재사용 시도")
    with ctx.factory() as s:
        row = s.get(orm.CheckSessionModel, uuid.UUID(sid))
        assert row is not None and row.thread_id == th
        row.deleted_at = NOW  # 부모(점검)가 삭제되면 스레드도 쓸 수 없다
        s.commit()
    with pytest.raises(NotFoundError):
        _call(ctx, ctx.user_a, th, "삭제된 스레드 접근")
    assert ctx.model.calls == []
    # 남의 점검으로 스레드 발급도 불가
    assert (
        ctx.client.post(
            "/api/v1/agent-threads", headers=ctx.auth(ctx.user_b), json={"check_session_id": sid}
        ).status_code
        == 404
    )


def test_db05_direct_case_thread_is_traceable(ctx: Ctx) -> None:
    agree_all(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    case_id = ctx.client.post(
        "/api/v1/cases", headers=h, json={"title": "신규 직행 사건", "case_type": "FITNESS_REFUND"}
    ).json()["id"]
    r = ctx.client.post("/api/v1/agent-threads", headers=h, json={"case_id": case_id})
    assert r.status_code == 201
    th = r.json()["thread_id"]
    _call(ctx, ctx.user_a, th, "사건에서 시작한 실행")  # 근거 없음 → 차단 기록도 추적 대상
    with ctx.factory() as s:
        case = s.get(orm.CaseModel, uuid.UUID(case_id))
        assert case is not None and case.thread_id == th and case.owner_id == ctx.user_a
        log = s.scalars(select(orm.AuditLogModel).where(orm.AuditLogModel.action == "AI_CALL")).one()
        assert log.target_type == "cases" and str(log.target_id) == case_id and log.actor_id == ctx.user_a
        assert log.detail is not None and log.detail["status"] == "BLOCKED"
    assert (
        ctx.client.post(
            "/api/v1/agent-threads", headers=h, json={"check_session_id": None, "case_id": None}
        ).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# DB-04 삭제·파기
# ---------------------------------------------------------------------------
def test_db04_purge_removes_db_rows_files_and_checkpoints(ctx: Ctx) -> None:
    sid, _, _ = _approved(ctx, ctx.user_a)
    case_id = _convert(ctx, ctx.user_a, sid)
    th = _thread(ctx, ctx.user_a, sid)
    files_before = list(ctx.files_dir.iterdir())
    assert len(files_before) == 1
    assert ctx.client.delete(f"/api/v1/cases/{case_id}", headers=ctx.auth(ctx.user_a)).status_code == 204

    ctx.clock.current = NOW + timedelta(days=10)  # 유예기간(30일) 전 → 남아 있어야 함
    assert ctx.container.purge_expired_data().execute().cases == 0
    assert _count(ctx, orm.CaseModel) == 1

    ctx.clock.current = NOW + timedelta(days=31)
    report = ctx.container.purge_expired_data().execute()
    assert report.cases == 1 and report.files == 1 and report.checkpoints == 1
    for model in (
        orm.CaseModel,
        orm.CheckSessionModel,
        orm.DocumentModel,
        orm.ExtractedFactModel,
        orm.InspectionReportModel,
        orm.EvidenceModel,
        orm.CaseEventModel,
    ):
        assert _count(ctx, model) == 0, model.__tablename__
    assert list(ctx.files_dir.iterdir()) == []  # 객체 저장소 파일 삭제
    assert ctx.checkpoints.deleted == [th]  # LangGraph 체크포인트 삭제 요청
    with ctx.factory() as s:
        logs = s.scalars(
            select(orm.AuditLogModel)
            .where(orm.AuditLogModel.action == "PURGE")
            .order_by(orm.AuditLogModel.created_at)
        ).all()
        assert len(logs) == 2  # 배치는 대상이 없어도 실행 사실을 남긴다
        zero = {"sessions": 0, "withdrawn_users": 0, "consents": 0, "audit_logs": 0, "accounts": 0}
        assert logs[0].detail == {**zero, "cases": 0, "files": 0, "threads": 0}
        # 건수만, 원문 없음(v0.9: 데이터별 보관 정책 건수 추가)
        assert logs[1].detail == {**zero, "cases": 1, "files": 1, "threads": 1}


def test_db04_document_is_not_purged_alone_v08(ctx: Ctx) -> None:
    """v0.8 묶음 보존(팀 결정 2 → ⑤): 문서 보관기한이 지나도 문서만 따로 지우지 않는다.

    v0.7 까지는 문서 단독 파기가 FK CASCADE 로 '확인된 사실'까지 지웠다(11/09 실사 P0-A).
    """
    sid, rid, doc = _approved(ctx, ctx.user_a)
    # v0.9: 미전환 점검은 30일 뒤 묶음째 파기되므로, 사건으로 전환해 '진행 중 사건'의 묶음으로 둔다
    _convert(ctx, ctx.user_a, sid)
    facts_before = _count(ctx, orm.ExtractedFactModel)
    ctx.clock.current = NOW + timedelta(days=181)  # 문서 retention_until(180일) 경과
    report = ctx.container.purge_expired_data().execute()
    assert report.cases == 0 and report.files == 0
    assert _count(ctx, orm.DocumentModel) == 1 and _count(ctx, orm.ExtractedFactModel) == facts_before
    assert len(list(ctx.files_dir.iterdir())) == 1
    assert sid and rid and doc
