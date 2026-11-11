"""v0.8 변경 회귀 테스트 (2026-11-09 팀 승인: 결정 1~5).

- G1 근거 보고서는 원본 점검의 보고서만 / G2 승인 보고서 본문 불변(PostgreSQL)
- G3 사용자 수정값을 '문서 근거'로 표시하지 않음 / G4 보고서 승인 전 재수정 허용(승인 후 차단)
- PDF 만으로 점검 시작(결정 3) / confirm_action 삭제 후 확인 컬럼 정합성(멘토 10번)
- 외부 모델 미설정 시 '외부 전송 없음' 기록 / RLS 앱 연결(HJ_TEST_APP_DATABASE_URL 모드)
문서 단독 파기 제거(결정 2 → ⑤)는 test_change_requests.py::test_db04_document_is_not_purged_alone_v08.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.application.use_cases.agent import ModelCallCommand
from app.domain import enums
from app.infrastructure.db import models as orm
from app.infrastructure.services import DisabledModelClient
from tests.conftest import Ctx, agree_all
from tests.test_change_requests import _approved, _convert, _overseas, _session, _thread, _upload

PG = "postgresql" in os.environ.get("HJ_TEST_DATABASE_URL", "")
RLS_MODE = PG and bool(os.environ.get("HJ_TEST_APP_DATABASE_URL"))
pg_only = pytest.mark.skipif(not PG, reason="PostgreSQL 전용(트리거·원시 SQL)")
rls_only = pytest.mark.skipif(not RLS_MODE, reason="RLS 모드(HJ_TEST_APP_DATABASE_URL) 전용")


def _facts(ctx: Ctx, user: uuid.UUID, sid: str, facts: list[dict]) -> list[str]:  # type: ignore[type-arg]
    r = ctx.client.post(f"/api/v1/check-sessions/{sid}/facts", headers=ctx.svc(user), json={"facts": facts})
    assert r.status_code == 201, r.text
    return list(r.json()["ids"])


def _confirm(ctx: Ctx, user: uuid.UUID, fid: str, body: dict) -> int:  # type: ignore[type-arg]
    return ctx.client.post(f"/api/v1/facts/{fid}/confirmation", headers=ctx.auth(user), json=body).status_code


# ---------------------------------------------------------------------------
# G1: 사건의 근거 보고서 = 원본 점검의 보고서
# ---------------------------------------------------------------------------
def test_g1_case_cannot_point_to_other_users_report(ctx: Ctx) -> None:
    _, rid_b, _ = _approved(ctx, ctx.user_b)
    sid_a = _session(ctx, ctx.user_a)
    with ctx.factory() as s, pytest.raises(IntegrityError):
        s.add(
            orm.CaseModel(
                id=uuid.uuid4(),
                owner_id=ctx.user_a,
                title="교차 연결",
                case_type=enums.CaseType.FITNESS_REFUND,
                status=enums.CaseStatus.OPEN,
                origin_session_id=uuid.UUID(sid_a),
                snapshot_report_id=uuid.UUID(rid_b),
                verified_facts={"facts": []},
                snapshot_version=1,
                snapshot_approved_at=datetime(2026, 11, 1, tzinfo=UTC),
                unverified_items=[],
            )
        )
        s.commit()


def test_g1_report_without_origin_is_rejected(ctx: Ctx) -> None:
    _, rid, _ = _approved(ctx, ctx.user_a)
    with ctx.factory() as s, pytest.raises(IntegrityError):
        s.add(
            orm.CaseModel(
                id=uuid.uuid4(),
                owner_id=ctx.user_a,
                title="원본 없음",
                case_type=enums.CaseType.FITNESS_REFUND,
                status=enums.CaseStatus.OPEN,
                snapshot_report_id=uuid.UUID(rid),
                unverified_items=[],
            )
        )
        s.commit()


def test_g1_normal_conversion_still_links_report(ctx: Ctx) -> None:
    sid, rid, _ = _approved(ctx, ctx.user_a)
    cid = _convert(ctx, ctx.user_a, sid)
    with ctx.factory() as s:
        c = s.get(orm.CaseModel, uuid.UUID(cid))
        assert c is not None and c.snapshot_report_id == uuid.UUID(rid)


# ---------------------------------------------------------------------------
# G2: 승인·대체 보고서 본문 불변(PostgreSQL 트리거)
# ---------------------------------------------------------------------------
@pg_only
def test_g2_approved_report_body_is_immutable(ctx: Ctx) -> None:
    _, rid, _ = _approved(ctx, ctx.user_a)
    for sql in (
        "UPDATE inspection_reports SET summary_text = '조작' WHERE id = :i",
        "UPDATE inspection_reports SET items = CAST('[]' AS JSONB) WHERE id = :i",
        "UPDATE inspection_reports SET status = 'DRAFT' WHERE id = :i",
    ):
        with ctx.factory() as s, pytest.raises(DBAPIError, match="immutable"):
            s.execute(text(sql), {"i": uuid.UUID(rid)})
            s.commit()


@pg_only
def test_g2_new_version_supersedes_and_purge_still_works(ctx: Ctx) -> None:
    sid, rid, _ = _approved(ctx, ctx.user_a)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={"summary_text": "v2", "items": [{"category": "UNVERIFIED", "content": "해지일 확인 필요"}]},
    )
    assert r.status_code == 201, r.text
    assert (
        ctx.client.post(
            f"/api/v1/reports/{r.json()['id']}/approval", headers=ctx.auth(ctx.user_a)
        ).status_code
        == 204
    )
    with ctx.factory() as s:
        v1 = s.get(orm.InspectionReportModel, uuid.UUID(rid))
        assert v1 is not None and v1.status == enums.ReportStatus.SUPERSEDED
        s.execute(
            text("DELETE FROM check_sessions WHERE id = :i"), {"i": uuid.UUID(sid)}
        )  # 파기(DELETE)는 허용
        s.commit()
        n = s.scalar(select(func.count()).select_from(orm.InspectionReportModel))
        assert n == 0


# ---------------------------------------------------------------------------
# G3: 사용자 수정값은 스냅샷에서 '진술(사용자 수정)'
# ---------------------------------------------------------------------------
def test_g3_snapshot_marks_user_modified_value(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    doc = _upload(ctx, ctx.user_a, sid)
    fid, fid2 = _facts(
        ctx,
        ctx.user_a,
        sid,
        [
            {
                "fact_key": "PAYMENT_AMOUNT",
                "value_text": "720,000원",
                "source_kind": "DOCUMENT",
                "document_id": doc,
                "page_no": 2,
                "offset_start": 233,
                "offset_end": 262,
            },
            {"fact_key": "PAYMENT_METHOD", "value_text": "카드", "source_kind": "STATEMENT"},
        ],
    )
    assert _confirm(ctx, ctx.user_a, fid, {"action": "MODIFY", "new_value": "700,000원"}) == 200
    assert _confirm(ctx, ctx.user_a, fid2, {"action": "CONFIRM"}) == 200
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "요약",
            "items": [{"category": "CONFIRMED_FACT", "content": "결제금액", "fact_id": fid}],
        },
    )
    assert r.status_code == 201, r.text
    assert (
        ctx.client.post(
            f"/api/v1/reports/{r.json()['id']}/approval", headers=ctx.auth(ctx.user_a)
        ).status_code
        == 204
    )
    cid = _convert(ctx, ctx.user_a, sid)
    with ctx.factory() as s:
        c = s.get(orm.CaseModel, uuid.UUID(cid))
        assert c is not None and c.verified_facts is not None
        amount = next(f for f in c.verified_facts["facts"] if f["fact_id"] == fid)
        method = next(f for f in c.verified_facts["facts"] if f["fact_id"] == fid2)
        assert amount["source_kind"] == "STATEMENT" and amount["user_modified"] is True
        assert amount["ai_source_kind"] == "DOCUMENT" and amount["value"] == "700,000원"
        assert method["source_kind"] == "STATEMENT" and method["user_modified"] is False
        # AI 의 원래 근거(문서·위치·값)는 추출 사실 행에 그대로 남는다
        f = s.get(orm.ExtractedFactModel, uuid.UUID(fid))
        assert f is not None and f.ai_value_text == "720,000원" and f.offset_start == 233


# ---------------------------------------------------------------------------
# G4: 보고서 승인 전 재수정 허용, 옛 사실로 만든 보고서는 승인 불가, 승인 후 수정 차단
# ---------------------------------------------------------------------------
def test_g4_can_correct_after_all_confirmed_before_report(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    (fid,) = _facts(
        ctx,
        ctx.user_a,
        sid,
        [{"fact_key": "PAYMENT_AMOUNT", "value_text": "720,000원", "source_kind": "STATEMENT"}],
    )
    assert _confirm(ctx, ctx.user_a, fid, {"action": "CONFIRM"}) == 200
    assert _confirm(ctx, ctx.user_a, fid, {"action": "MODIFY", "new_value": "700,000원"}) == 200
    with ctx.factory() as s:
        f = s.get(orm.ExtractedFactModel, uuid.UUID(fid))
        sess = s.get(orm.CheckSessionModel, uuid.UUID(sid))
        assert f is not None and f.value_text == "700,000원" and f.status == enums.FactStatus.MODIFIED
        assert sess is not None and sess.status == enums.SessionStatus.FACTS_CONFIRMED


def test_g4_report_drafted_before_correction_cannot_be_approved(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    (fid,) = _facts(
        ctx,
        ctx.user_a,
        sid,
        [{"fact_key": "PAYMENT_AMOUNT", "value_text": "720,000원", "source_kind": "STATEMENT"}],
    )
    assert _confirm(ctx, ctx.user_a, fid, {"action": "CONFIRM"}) == 200
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={"summary_text": "옛 사실 기준", "items": [{"category": "UNVERIFIED", "content": "확인 필요"}]},
    )
    assert r.status_code == 201
    old_report = r.json()["id"]
    ctx.clock.current = ctx.clock.current.replace(minute=5)  # 보고서 작성 뒤 사실 재수정
    assert _confirm(ctx, ctx.user_a, fid, {"action": "MODIFY", "new_value": "700,000원"}) == 200
    assert (
        ctx.client.post(f"/api/v1/reports/{old_report}/approval", headers=ctx.auth(ctx.user_a)).status_code
        == 409
    )
    r2 = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={"summary_text": "새 사실 기준", "items": [{"category": "UNVERIFIED", "content": "확인 필요"}]},
    )
    assert (
        ctx.client.post(
            f"/api/v1/reports/{r2.json()['id']}/approval", headers=ctx.auth(ctx.user_a)
        ).status_code
        == 204
    )


def test_g4_facts_still_frozen_after_report_approval(ctx: Ctx) -> None:
    sid, _, _ = _approved(ctx, ctx.user_a)
    with ctx.factory() as s:
        fid = s.scalars(
            select(orm.ExtractedFactModel.id).where(orm.ExtractedFactModel.check_session_id == uuid.UUID(sid))
        ).first()
    assert _confirm(ctx, ctx.user_a, str(fid), {"action": "MODIFY", "new_value": "1원"}) == 409


# ---------------------------------------------------------------------------
# 결정 3: PDF 만으로 점검 시작
# ---------------------------------------------------------------------------
def test_pdf_only_entry_flow(ctx: Ctx) -> None:
    agree_all(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    r = ctx.client.post("/api/v1/check-sessions", headers=h, json={"entry_type": "DISPUTE_OCCURRED"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    fact = {"fact_key": "PAYMENT_METHOD", "value_text": "카드", "source_kind": "STATEMENT"}
    # PDF 도 설명도 없으면 사실 등록 불가
    blocked = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts", headers=ctx.svc(ctx.user_a), json={"facts": [fact]}
    )
    assert blocked.status_code == 422
    doc = _upload(ctx, ctx.user_a, sid)
    ok = _facts(
        ctx,
        ctx.user_a,
        sid,
        [
            {
                "fact_key": "PAYMENT_AMOUNT",
                "value_text": "720,000원",
                "source_kind": "DOCUMENT",
                "document_id": doc,
            }
        ],
    )
    assert len(ok) == 1
    with ctx.factory() as s:
        sess = s.get(orm.CheckSessionModel, uuid.UUID(sid))
        assert sess is not None and sess.situation_text is None


def test_text_rules_still_apply_when_text_is_given(ctx: Ctx) -> None:
    agree_all(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    for bad in ("", "짧음", "x" * 4001):
        r = ctx.client.post(
            "/api/v1/check-sessions",
            headers=h,
            json={"entry_type": "DISPUTE_OCCURRED", "situation_text": bad},
        )
        assert r.status_code == 422, bad


# ---------------------------------------------------------------------------
# 멘토 10번: confirm_action 삭제 후 확인 컬럼 정합성
# ---------------------------------------------------------------------------
def test_confirm_action_column_removed_and_status_rules_hold(ctx: Ctx) -> None:
    assert "confirm_action" not in orm.ExtractedFactModel.__table__.columns
    sid = _session(ctx, ctx.user_a)
    with ctx.factory() as s, pytest.raises(IntegrityError):
        s.add(  # 미확인(EXTRACTED)인데 확인일시가 있으면 거부
            orm.ExtractedFactModel(
                id=uuid.uuid4(),
                check_session_id=uuid.UUID(sid),
                fact_key="BUSINESS_NAME",
                value_text="A센터",
                source_kind="STATEMENT",
                status="EXTRACTED",
                confirmed_by=ctx.user_a,
                confirmed_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
        )
        s.commit()


# ---------------------------------------------------------------------------
# 감사 기록 정확성: 모델 미설정이면 '외부 전송 없음'
# ---------------------------------------------------------------------------
def test_unconfigured_model_is_logged_as_not_sent(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    _overseas(ctx, ctx.user_a, True)
    th = _thread(ctx, ctx.user_a, sid)
    ctx.container.model_client = DisabledModelClient()
    out = (
        ctx.container.for_user(ctx.user_a)
        .invoke_external_model()
        .execute(ModelCallCommand(ctx.user_a, th, "N1", "필라테스 환불 문의"))
    )
    assert out.allowed is False
    with ctx.factory() as s:
        log = s.scalars(select(orm.AuditLogModel).where(orm.AuditLogModel.action == "AI_CALL")).one()
        assert log.detail is not None
        assert log.detail["external_api"] is False and log.detail["code"] == "MODEL_NOT_CONFIGURED"
        assert log.detail["transfer_basis"] == "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# RLS 앱 연결 (HJ_TEST_APP_DATABASE_URL 로 비관리자 롤 접속 시)
# ---------------------------------------------------------------------------
@rls_only
def test_rls_request_user_is_bound_and_others_invisible(ctx: Ctx) -> None:
    _approved(ctx, ctx.user_a)
    _approved(ctx, ctx.user_b)
    uow_a = ctx.container.for_user(ctx.user_a)._uow()
    with uow_a as uow:
        s = uow._require()  # type: ignore[attr-defined]
        assert s.execute(text("SELECT current_setting('app.user_id', true)")).scalar() == str(ctx.user_a)
        # 소유자 조건을 일부러 빼고 전체를 조회해도 자기 점검만 보인다(앱 코드 실수 대비 3차 방어선)
        owners = set(s.scalars(select(orm.CheckSessionModel.user_id)).all())
        assert owners == {ctx.user_a}
    with ctx.container._uow() as uow:  # 사용자 없이 접속 → 0건(기본 거부)
        assert uow._require().scalar(select(func.count()).select_from(orm.CheckSessionModel)) == 0  # type: ignore[attr-defined]
