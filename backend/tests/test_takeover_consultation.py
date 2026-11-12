"""날짜 미확인 상담 초안(FB02-F1)의 실제 API 회귀시험.

합성 SYN-001, 모델 추출 대역: 필라테스 6개월·60만원·6회 오류를 4회로 수정.
PDF는 제출하지 않는다. 계약서 사본 보유 여부는 UNKNOWN이며 사본 없음이 아니다.
"""

from __future__ import annotations

import copy
import uuid

from app.infrastructure.db import models as orm
from tests.conftest import Ctx, agree_all

API = "/api/v1"


def _converted_case_without_dates(ctx: Ctx) -> tuple[str, str, list[str]]:
    h = ctx.auth(ctx.user_a)
    agree_all(ctx, ctx.user_a)
    r = ctx.client.post(
        f"{API}/check-sessions",
        headers=h,
        json={
            "entry_type": "REFUND_CONCERN",
            "situation_text": "필라테스 6개월 60만원을 결제하고 4회 이용했습니다. 날짜는 모릅니다.",
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    known = [
        {"fact_key": "BUSINESS_NAME", "value_text": "합성 필라테스", "source_kind": "STATEMENT"},
        {"fact_key": "CONTRACT_PERIOD", "value_text": "6개월", "source_kind": "STATEMENT"},
        {"fact_key": "PAYMENT_AMOUNT", "value_text": "600,000원", "source_kind": "STATEMENT"},
        {"fact_key": "USED_SESSIONS", "value_text": "6회", "source_kind": "STATEMENT"},
    ]
    unknown = [
        {"fact_key": "CONTRACT_DATE", "source_kind": "UNKNOWN"},
        {"fact_key": "CANCEL_REQUEST_DATE", "source_kind": "UNKNOWN"},
        {"fact_key": "OTHER", "source_kind": "UNKNOWN"},
    ]
    r = ctx.client.post(
        f"{API}/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={"facts": known + unknown},
    )
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    for index, fid in enumerate(ids):
        body = (
            {"action": "MODIFY", "new_value": "4회"}
            if index == 3
            else {"action": "CONFIRM" if index < 4 else "ACKNOWLEDGE_UNKNOWN"}
        )
        response = ctx.client.post(f"{API}/facts/{fid}/confirmation", headers=h, json=body)
        assert response.status_code == 200, response.text
    questions = ["계약일 미확인", "해지 요청일 미확인", "계약서 사본 보유 여부 미확인"]
    items = [
        {"category": "CONFIRMED_FACT", "content": value, "fact_id": fid}
        for fid, value in zip(ids[:4], ["합성 필라테스", "6개월", "600,000원", "4회"], strict=True)
    ] + [
        {"category": "UNVERIFIED", "content": question, "fact_id": fid}
        for fid, question in zip(ids[4:], questions, strict=True)
    ]
    response = ctx.client.post(
        f"{API}/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={"summary_text": "확인된 사실과 추가 확인할 항목을 정리했습니다.", "items": items},
    )
    assert response.status_code == 201, response.text
    rid = response.json()["id"]
    assert ctx.client.post(f"{API}/reports/{rid}/approval", headers=h).status_code == 204
    response = ctx.client.post(
        f"{API}/check-sessions/{sid}/case-conversion", headers=h, json={"title": "합성 SYN-001 상담 준비"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], rid, questions


def test_known_facts_with_zero_date_events_can_prepare_without_inventing_dates(ctx: Ctx) -> None:
    """문제 재현과 수정 검증: 기존 코드는 상담 준비 요청에서 422로 실패한다."""
    cid, rid, questions = _converted_case_without_dates(ctx)
    h = ctx.auth(ctx.user_a)
    before = ctx.client.get(f"{API}/cases/{cid}", headers=h).json()
    snapshot = copy.deepcopy(before["snapshot"])
    assert before["events"] == []
    assert len(snapshot["facts"]) == 4
    with ctx.factory() as s:
        approved = s.get(orm.InspectionReportModel, uuid.UUID(rid))
        assert approved is not None
        report_before = (approved.status, approved.summary_text, approved.approved_at)

    # 실제 HTTP 요청 경로와 영속 저장 결과를 확인한다. 외부 모델은 대역이다.
    for version in (1, 2):
        response = ctx.client.post(f"{API}/cases/{cid}/consultation-preps", headers=h, json={})
        assert response.status_code == 201, response.text
        assert response.json() == {"case_id": cid, "version": version}
        with ctx.factory() as s:
            case = s.get(orm.CaseModel, uuid.UUID(cid))
            assert case is not None and case.consultation_prep is not None
            prep = case.consultation_prep
            assert prep["version"] == version and prep["status"] == "DRAFT"
            assert "날짜 미확인" in prep["summary_text"]
            for value in ("6개월", "600,000원", "4회", "사용자 진술"):
                assert value in prep["summary_text"]
            assert "6회" not in prep["summary_text"]
            assert "사본 없음" not in prep["summary_text"]
            assert prep["questions"] == [f"확인 필요: {q}" for q in questions]
            assert prep["checklist"] == []
            assert case.verified_facts == snapshot
            approved = s.get(orm.InspectionReportModel, uuid.UUID(rid))
            assert approved is not None
            assert (approved.status, approved.summary_text, approved.approved_at) == report_before
        after = ctx.client.get(f"{API}/cases/{cid}", headers=h).json()
        assert after["events"] == [] and after["snapshot"] == snapshot
        assert after["unverified"] == questions
        assert after["evidences"] == []

    # 무이벤트 허용이 본인 자료 권한이나 기관 검증을 우회하지 않는다.
    assert (
        ctx.client.post(f"{API}/cases/{cid}/consultation-preps", headers=ctx.auth(ctx.user_b), json={})
        .status_code
        == 404
    )
    assert (
        ctx.client.post(
            f"{API}/cases/{cid}/consultation-preps", headers=h, json={"agency_code": "UNKNOWN_AGENCY"}
        ).status_code
        == 404
    )


def test_empty_manual_case_without_confirmed_snapshot_still_cannot_prepare(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    response = ctx.client.post(
        f"{API}/cases", headers=h, json={"title": "내용 없는 합성 사건", "case_type": "FITNESS_REFUND"}
    )
    assert response.status_code == 201, response.text
    cid = response.json()["id"]
    response = ctx.client.post(f"{API}/cases/{cid}/consultation-preps", headers=h, json={})
    assert response.status_code == 422, response.text
    with ctx.factory() as s:
        case = s.get(orm.CaseModel, uuid.UUID(cid))
        assert case is not None and case.consultation_prep is None
