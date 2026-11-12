"""인수 비교에서 확인한 UNKNOWN·상태 경계의 최소 회귀시험.

DB 상태를 직접 바꾸는 시험은 격리 fixture에서 기존/불일치 자료를 재현한다.
정상 사용자 API로 그 불일치 상태가 만들어진다는 뜻은 아니다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.infrastructure.db import models as orm
from tests.conftest import Ctx, agree_all
from tests.test_v011 import _confirm, _report, _session_with_unknown

API = "/api/v1"


def _start(ctx: Ctx) -> str:
    agree_all(ctx, ctx.user_a)
    response = ctx.client.post(
        f"{API}/check-sessions",
        headers=ctx.auth(ctx.user_a),
        json={"entry_type": "REFUND_CONCERN", "situation_text": "필라테스 계약 날짜를 모릅니다."},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.parametrize(
    "metadata",
    [
        {"value_normalized": {"date": "2026-05-10"}},
        {"value_normalized": {}},
        {"page_no": 1},
        {"offset_start": 0, "offset_end": 0},
        {"source_span": "추정된 원문"},
        {"source_span": ""},
    ],
    ids=["normalized-date", "normalized-empty", "page", "offsets", "span", "span-empty"],
)
def test_unknown_rejects_non_null_metadata_without_partial_writes(
    ctx: Ctx, metadata: dict[str, Any]
) -> None:
    sid = _start(ctx)
    response = ctx.client.post(
        f"{API}/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {"fact_key": "PAYMENT_AMOUNT", "value_text": "600,000원", "source_kind": "STATEMENT"},
                {"fact_key": "CONTRACT_DATE", "source_kind": "UNKNOWN", **metadata},
            ]
        },
    )
    assert response.status_code == 422, response.text
    with ctx.factory() as db:
        count = db.scalar(
            select(func.count()).select_from(orm.ExtractedFactModel).where(
                orm.ExtractedFactModel.check_session_id == uuid.UUID(sid)
            )
        )
        session = db.get(orm.CheckSessionModel, uuid.UUID(sid))
        assert count == 0
        assert session is not None and session.status == "DRAFT"


def test_unknown_explicit_null_metadata_stays_null_after_ack(ctx: Ctx) -> None:
    sid = _start(ctx)
    fields = (
        "value_text",
        "value_normalized",
        "document_id",
        "page_no",
        "offset_start",
        "offset_end",
        "source_span",
    )
    response = ctx.client.post(
        f"{API}/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {"fact_key": "CONTRACT_DATE", "source_kind": "UNKNOWN", **dict.fromkeys(fields)}
            ]
        },
    )
    assert response.status_code == 201, response.text
    fid = response.json()["ids"][0]
    assert _confirm(ctx, ctx.user_a, fid, action="ACKNOWLEDGE_UNKNOWN").status_code == 200
    with ctx.factory() as db:
        fact = db.get(orm.ExtractedFactModel, uuid.UUID(fid))
        assert fact is not None and fact.status == "ACKNOWLEDGED_UNKNOWN"
        assert all(getattr(fact, field) is None for field in (*fields, "ai_value_text"))


def _confirmed(ctx: Ctx) -> tuple[str, str, str, list[dict[str, Any]]]:
    sid, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    assert _confirm(ctx, ctx.user_a, known[0], action="CONFIRM").status_code == 200
    assert _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN").status_code == 200
    items = [
        {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": known[0]},
        {"category": "UNVERIFIED", "content": "해지 요청일 미확인", "fact_id": unknown[0]},
    ]
    return sid, known[0], unknown[0], items


def _set_inconsistent_fact(ctx: Ctx, fid: str, *, pending: bool) -> None:
    # 기존 DB CHECK는 보존한다. 미처리(EXTRACTED) 행의 처리자/시각도 비워야 유효하다.
    values: dict[str, Any] = {"status": "EXTRACTED" if pending else "REJECTED"}
    if pending:
        values.update(confirmed_by=None, confirmed_at=None)
    with ctx.factory() as db:
        db.execute(
            update(orm.ExtractedFactModel)
            .where(orm.ExtractedFactModel.id == uuid.UUID(fid))
            .values(**values)
        )
        db.commit()


@pytest.mark.parametrize("inconsistency", ["pending", "no-known"])
def test_report_rechecks_facts_when_session_flag_is_inconsistent(ctx: Ctx, inconsistency: str) -> None:
    sid, known, unknown, items = _confirmed(ctx)
    # 격리된 기존자료 불일치 재현: 세션 상태는 확인 완료이나 사실 행 상태는 다르다.
    _set_inconsistent_fact(
        ctx, unknown if inconsistency == "pending" else known, pending=inconsistency == "pending"
    )
    if inconsistency == "no-known":
        items = items[1:]
    response = _report(ctx, ctx.user_a, sid, items)
    assert response.status_code == 409, response.text
    with ctx.factory() as db:
        assert db.scalar(select(func.count()).select_from(orm.InspectionReportModel)) == 0


def test_approved_session_rejects_confirmation_before_fact_mutation(ctx: Ctx) -> None:
    sid, known, unknown, items = _confirmed(ctx)
    draft = _report(ctx, ctx.user_a, sid, items)
    assert draft.status_code == 201, draft.text
    rid = draft.json()["id"]
    approved = ctx.client.post(f"{API}/reports/{rid}/approval", headers=ctx.auth(ctx.user_a))
    assert approved.status_code == 204
    # CONFIRM 자체도 UNKNOWN에는 불가지만, 승인 후 세션 경계의 409가 먼저 적용되어야 한다.
    response = _confirm(ctx, ctx.user_a, unknown, action="CONFIRM")
    assert response.status_code == 409, response.text
    detail = ctx.client.get(f"{API}/reports/{rid}", headers=ctx.auth(ctx.user_a)).json()
    assert detail["status"] == "APPROVED"
    assert [item["fact_id"] for item in detail["items"]] == [known, unknown]
    with ctx.factory() as db:
        fact = db.get(orm.ExtractedFactModel, uuid.UUID(unknown))
        assert fact is not None and fact.status == "ACKNOWLEDGED_UNKNOWN"


@pytest.mark.parametrize("inconsistency", ["pending", "no-known"])
def test_case_conversion_rechecks_facts_before_snapshot(ctx: Ctx, inconsistency: str) -> None:
    sid, known, unknown, items = _confirmed(ctx)
    draft = _report(ctx, ctx.user_a, sid, items)
    assert draft.status_code == 201
    approved = ctx.client.post(
        f"{API}/reports/{draft.json()['id']}/approval", headers=ctx.auth(ctx.user_a)
    )
    assert approved.status_code == 204
    # 승인본은 그대로 보존하고 기존 사실 행만 불일치한 격리 자료를 만든다.
    _set_inconsistent_fact(
        ctx, unknown if inconsistency == "pending" else known, pending=inconsistency == "pending"
    )
    response = ctx.client.post(
        f"{API}/check-sessions/{sid}/case-conversion",
        headers=ctx.auth(ctx.user_a),
        json={"title": "필라테스 환불"},
    )
    assert response.status_code == 409, response.text
    with ctx.factory() as db:
        assert db.scalar(select(func.count()).select_from(orm.CaseModel)) == 0
        session = db.get(orm.CheckSessionModel, uuid.UUID(sid))
        assert session is not None and session.status == "REPORTED"
