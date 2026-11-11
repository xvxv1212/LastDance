"""필라테스 환불 시나리오 전체 흐름(E2E) 테스트.

상황 입력 → 계약서 업로드 → 사실 추출 → 사용자 확인 → 보고서 → 승인 → 사건 전환 →
증거·타임라인 → 상담 준비자료 → 사건 조회
"""

from __future__ import annotations

from tests.conftest import Ctx, agree_all

PDF = b"%PDF-1.4\n% test contract\n"


def _run_until_report(ctx: Ctx) -> tuple[str, list[str], str]:
    h = ctx.auth(ctx.user_a)
    agree_all(ctx, ctx.user_a)

    r = ctx.client.post(
        "/api/v1/check-sessions",
        headers=h,
        json={
            "entry_type": "REFUND_CONCERN",
            "situation_text": (
                "필라테스 30회권 결제 후 10회만 이용했는데 환불이 안 된대요. 연락처 010-1234-5678"
            ),
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/documents",
        headers=h,
        files={"file": ("계약서.pdf", PDF, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]

    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {
                    "fact_key": "CONTRACT_DATE",
                    "value_text": "2026년 3월 2일",
                    "source_kind": "DOCUMENT",
                    "document_id": doc_id,
                    "page_no": 1,
                    "value_normalized": {"date": "2026-03-02"},
                },
                {
                    "fact_key": "PAYMENT_AMOUNT",
                    "value_text": "1,200,000원",
                    "source_kind": "DOCUMENT",
                    "document_id": doc_id,
                    "page_no": 1,
                    "value_normalized": {"amount": 1200000},
                },
                {"fact_key": "PAYMENT_AMOUNT", "value_text": "1,000,000원", "source_kind": "STATEMENT"},
                {"fact_key": "BUSINESS_NAME", "value_text": "OO필라테스", "source_kind": "STATEMENT"},
                {
                    "fact_key": "CANCEL_REQUEST_DATE",
                    "value_text": "10월 20일",
                    "source_kind": "STATEMENT",
                    "value_normalized": {"date": "2026-10-20"},
                },
            ]
        },
    )
    assert r.status_code == 201, r.text
    fact_ids = r.json()["ids"]
    return sid, fact_ids, doc_id


def test_full_flow(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    sid, fact_ids, _ = _run_until_report(ctx)

    # 사용자 확인: 계약서 금액은 맞음, 진술 금액은 틀림(거부)
    actions = ["CONFIRM", "CONFIRM", "REJECT", "CONFIRM", "CONFIRM"]
    status = None
    for fid, act in zip(fact_ids, actions, strict=True):
        r = ctx.client.post(f"/api/v1/facts/{fid}/confirmation", headers=h, json={"action": act})
        assert r.status_code == 200, r.text
        status = r.json()["session_status"]
    assert status == "FACTS_CONFIRMED"

    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "계약·결제 사실은 확인되었고, 환급 기준 적용 여부는 상담기관 확인이 필요합니다.",
            "items": [
                {"category": "CONFIRMED_FACT", "content": "계약일 2026-03-02", "fact_id": fact_ids[0]},
                {
                    "category": "CAUTION",
                    "content": (
                        "체육시설 중도 해지 환급 기준이 있으므로 계약서 환불 조항과 비교가 필요합니다."
                    ),
                    "citations": [
                        {
                            "source_chunk_id": str(ctx.chunk_id),
                            "quoted_text": "환급 기준",
                        }
                    ],
                },
                {"category": "UNVERIFIED", "content": "사업자의 환불 거절 사유(서면 여부)"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["held_items"] == 0  # 실제 청크 문구를 인용 → 서버 검증 통과
    report_id = r.json()["id"]
    assert ctx.client.post(f"/api/v1/reports/{report_id}/approval", headers=h).status_code == 204

    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/case-conversion", headers=h, json={"title": "필라테스 30회권 환불"}
    )
    assert r.status_code == 201, r.text
    case_id = r.json()["id"]

    # 같은 점검을 다시 전환해도 사건은 1건 — 기존 사건을 그대로 돌려준다(멱등, DB-02)
    again = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/case-conversion", headers=h, json={"title": "중복 전환"}
    )
    assert again.status_code == 200 and again.json() == {"id": case_id, "created": False}

    r = ctx.client.post(
        f"/api/v1/cases/{case_id}/evidences",
        headers=h,
        json={"evidence_type": "MESSAGE", "title": "환불 거절 문자", "status": "MISSING"},
    )
    assert r.status_code == 201, r.text

    r = ctx.client.post(
        f"/api/v1/cases/{case_id}/consultation-preps", headers=h, json={"agency_code": ctx.agency_code}
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 1

    d = ctx.client.get(f"/api/v1/cases/{case_id}", headers=h).json()
    assert d["status"] == "PREPARING"
    assert d["business_name"] == "OO필라테스"
    assert [e["title"] for e in d["events"]] == ["회원권 계약 체결", "해지·환불 요청"]
    assert {e["evidence_type"] for e in d["evidences"]} == {"CONTRACT", "MESSAGE"}
    assert d["unverified"] == ["사업자의 환불 거절 사유(서면 여부)"]
    assert len(d["snapshot"]["facts"]) == 4  # 거부된 1건 제외


def test_conflicting_facts_are_flagged(ctx: Ctx) -> None:
    """같은 항목(결제금액)에 서로 다른 값 → has_conflict = true"""
    from sqlalchemy import select

    from app.infrastructure.db import models as orm

    _run_until_report(ctx)
    # 시험용 직접 조회는 관리자 접속(ctx.factory)으로 한다. 앱 접속은 RLS 모드에서 사용자 없이 0건이 정상.
    with ctx.factory() as s:
        rows = s.scalars(
            select(orm.ExtractedFactModel).where(orm.ExtractedFactModel.fact_key == "PAYMENT_AMOUNT")
        )
        assert all(r.has_conflict for r in rows)


def test_personal_info_is_masked(ctx: Ctx) -> None:
    from sqlalchemy import select

    from app.infrastructure.db import models as orm

    _run_until_report(ctx)
    # 시험용 직접 조회는 관리자 접속(ctx.factory)으로 한다. 앱 접속은 RLS 모드에서 사용자 없이 0건이 정상.
    with ctx.factory() as s:
        text = s.scalars(select(orm.CheckSessionModel.situation_text)).one()
    assert "010-1234-5678" not in text and "[휴대전화]" in text
