"""상담 준비 화면(S13)용 최신 저장자료 조회와 시민 접근 경계.

합성 자료와 pytest 임시 SQLite만 사용한다. 화면 자체의 동작이나 실제 모델 검증은 아니다.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.domain.errors import AuthenticationError, NotFoundError
from app.infrastructure.db import models as orm
from app.infrastructure.knowledge import JsonAgencyDirectory
from tests.conftest import PASSWORD, Ctx
from tests.test_takeover_consultation import _converted_case_without_dates

API = "/api/v1"


def _prepare(ctx: Ctx) -> tuple[str, str]:
    cid, rid, _ = _converted_case_without_dates(ctx)
    response = ctx.client.post(
        f"{API}/cases/{cid}/consultation-preps", headers=ctx.auth(ctx.user_a), json={}
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"case_id": cid, "version": 1}
    return cid, rid


def test_get_returns_latest_stored_prep_without_regeneration_or_approval(ctx: Ctx) -> None:
    cid, rid, questions = _converted_case_without_dates(ctx)
    h = ctx.auth(ctx.user_a)
    url = f"{API}/cases/{cid}/consultation-preps"
    evidence_ids = []
    for status in ("HELD", "REQUESTED"):
        # 별도 합성 결제자료의 확보일이다. 모르는 계약일·해지일을 채우지 않는다.
        evidence = {
            "evidence_type": "PAYMENT_RECEIPT",
            "title": f"합성 결제자료 {status}",
            "status": status,
            "obtained_on": ctx.clock.now().date().isoformat() if status == "HELD" else None,
        }
        response = ctx.client.post(
            f"{API}/cases/{cid}/evidences",
            headers=h,
            json=evidence,
        )
        assert response.status_code == 201, response.text
        evidence_ids.append(response.json()["id"])
    post = ctx.client.post(url, headers=h, json={"agency_code": ctx.agency_code})
    assert post.status_code == 201 and post.json() == {"case_id": cid, "version": 1}
    with ctx.factory() as db:
        case = db.get(orm.CaseModel, uuid.UUID(cid))
        report = db.get(orm.InspectionReportModel, uuid.UUID(rid))
        assert case is not None and case.consultation_prep is not None and report is not None
        stored = copy.deepcopy(case.consultation_prep)
        original = (case.updated_at, copy.deepcopy(case.verified_facts), case.snapshot_version)
        report_original = (report.status, report.summary_text, report.approved_at)

    # 기관 목록이 바뀌어도 작성 당시 저장한 기관명·본문을 그대로 반환해야 한다.
    agencies = ctx.container.agencies
    ctx.container.agencies = JsonAgencyDirectory([])
    for _ in range(2):
        response = ctx.client.get(url, headers=h)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["case_id"] == cid
        assert set(body) == {"case_id", *stored}
        for key in stored.keys() - {"created_at"}:
            assert body[key] == stored[key], key
        assert datetime.fromisoformat(body["created_at"]) == datetime.fromisoformat(stored["created_at"])
        assert body["status"] == "DRAFT" and body["approved_at"] is None
        assert "날짜 미확인" in body["summary_text"] and "4회" in body["summary_text"]
        assert "6회" not in body["summary_text"] and "사본 없음" not in body["summary_text"]
        assert body["questions"] == [f"확인 필요: {item}" for item in questions]
        assert [item["evidence_id"] for item in body["checklist"]] == evidence_ids
        assert [item["ready"] for item in body["checklist"]] == [True, False]
        assert body["agency_name"] == stored["agency_name"] and body["agency_code"] == ctx.agency_code
        for excluded in (
            "storage_key", "thread_id", "password", "access_token", "owner_id", "verified_facts"
        ):
            assert excluded not in body
    ctx.container.agencies = agencies
    with ctx.factory() as db:
        case = db.get(orm.CaseModel, uuid.UUID(cid))
        report = db.get(orm.InspectionReportModel, uuid.UUID(rid))
        assert case is not None and report is not None
        assert case.consultation_prep == stored
        assert (case.updated_at, case.verified_facts, case.snapshot_version) == original
        assert (report.status, report.summary_text, report.approved_at) == report_original
        reads = db.scalars(
            select(orm.AuditLogModel).where(
                orm.AuditLogModel.action == "READ_SENSITIVE",
                orm.AuditLogModel.target_type == "cases.consultation_prep",
                orm.AuditLogModel.target_id == uuid.UUID(cid),
            )
        ).all()
        assert len(reads) == 2
        assert all(item.actor_id == ctx.user_a and item.detail == {"version": 1} for item in reads)

    # 새로 생성했을 때에만 최신 버전이 달라진다. 기존 POST 응답 형태는 유지한다.
    ctx.clock.current += timedelta(minutes=1)
    post = ctx.client.post(url, headers=h, json={})
    assert post.status_code == 201 and post.json() == {"case_id": cid, "version": 2}
    latest = ctx.client.get(url, headers=h).json()
    assert latest["version"] == 2 and latest["status"] == "DRAFT"
    assert latest["agency_code"] is None and latest["agency_name"] is None
    assert latest["created_at"] != body["created_at"]
    case_detail = ctx.client.get(f"{API}/cases/{cid}", headers=h).json()
    assert "consultation_prep" not in case_detail  # 기존 사건상세 응답 계약 보존
    assert case_detail["events"] == [] and case_detail["snapshot"] == original[1]


def test_missing_foreign_and_deleted_preps_share_404_without_creating_data(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    cid, _, _ = _converted_case_without_dates(ctx)
    missing = ctx.client.get(f"{API}/cases/{cid}/consultation-preps", headers=h)
    unknown = ctx.client.get(f"{API}/cases/{uuid.uuid4()}/consultation-preps", headers=h)
    assert missing.status_code == unknown.status_code == 404
    assert missing.json() == unknown.json()
    with ctx.factory() as db:
        case = db.get(orm.CaseModel, uuid.UUID(cid))
        assert case is not None and case.consultation_prep is None
    assert ctx.client.post(f"{API}/cases/{cid}/consultation-preps", headers=h, json={}).status_code == 201
    foreign = ctx.client.get(f"{API}/cases/{cid}/consultation-preps", headers=ctx.auth(ctx.user_b))
    assert foreign.status_code == 404 and foreign.json() == missing.json()
    assert ctx.client.delete(f"{API}/cases/{cid}", headers=h).status_code == 204
    deleted = ctx.client.get(f"{API}/cases/{cid}/consultation-preps", headers=h)
    assert deleted.status_code == 404 and deleted.json() == missing.json()
    with pytest.raises(NotFoundError):
        ctx.container.for_user(ctx.user_a).get_consultation_prep().execute(ctx.user_a, uuid.UUID(cid))


def test_get_requires_citizen_auth_and_does_not_lend_write_scopes(ctx: Ctx) -> None:
    cid, _ = _prepare(ctx)
    url = f"{API}/cases/{cid}/consultation-preps"
    assert ctx.client.get(url).status_code == 401
    assert ctx.client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert ctx.client.get(url, headers=ctx.svc(ctx.user_a)).status_code == 401
    assert ctx.client.get(url, headers=ctx.auth(ctx.user_b)).status_code == 404
    assert ctx.client.get(url, headers=ctx.auth(ctx.user_a)).status_code == 200
    with pytest.raises(NotFoundError):
        ctx.container.for_user(ctx.user_b).get_consultation_prep().execute(ctx.user_b, uuid.UUID(cid))


@pytest.mark.parametrize("inactive", ["dormant", "deleted"])
def test_inactive_user_is_blocked_in_api_and_direct_use_case(ctx: Ctx, inactive: str) -> None:
    cid, _ = _prepare(ctx)
    h = ctx.auth(ctx.user_a)
    with ctx.factory() as db:
        values = {"status": "DORMANT"} if inactive == "dormant" else {"deleted_at": ctx.clock.now()}
        db.execute(update(orm.UserModel).where(orm.UserModel.id == ctx.user_a).values(**values))
        db.commit()
    response = ctx.client.get(f"{API}/cases/{cid}/consultation-preps", headers=h)
    assert response.status_code == 401
    with pytest.raises(AuthenticationError):
        ctx.container.for_user(ctx.user_a).get_consultation_prep().execute(ctx.user_a, uuid.UUID(cid))


def test_withdrawal_revokes_existing_citizen_token_for_prep_read(ctx: Ctx) -> None:
    cid, _ = _prepare(ctx)
    h = ctx.auth(ctx.user_a)
    assert ctx.client.get(f"{API}/cases/{cid}/consultation-preps", headers=h).status_code == 200
    response = ctx.client.post(f"{API}/me/withdrawal", headers=h, json={"password": PASSWORD})
    assert response.status_code == 204, response.text
    assert ctx.client.get(f"{API}/cases/{cid}/consultation-preps", headers=h).status_code == 401
    with pytest.raises(AuthenticationError):
        ctx.container.for_user(ctx.user_a).get_consultation_prep().execute(ctx.user_a, uuid.UUID(cid))
