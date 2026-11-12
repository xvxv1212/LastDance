"""UC-18 사건에 PDF 추가 첨부 — 2026.11.04 팀 결정(증빙 이미지 제외 A안 + 사건 PDF 추가 첨부).

점검 업로드(UC-03)와 같은 규칙이 사건 쪽에도 적용되는지, 첨부 파일이 증거 연결·파기까지
기존 흐름에 그대로 들어가는지 확인한다.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.infrastructure.db import models as orm
from tests.conftest import NOW, Ctx, agree_all

PDF = b"%PDF-1.4\n% synthetic refund rejection notice\n"


def _new_case(ctx: Ctx, user: uuid.UUID, consent: bool = True) -> str:
    if consent:
        agree_all(ctx, user)
    r = ctx.client.post(
        "/api/v1/cases",
        headers=ctx.auth(user),
        json={"title": "필라테스 환불 (신규 사건)", "case_type": "FITNESS_REFUND"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _attach(ctx: Ctx, user: uuid.UUID, case_id: str, name: str = "환불거절_공문.pdf", body: bytes = PDF):  # type: ignore[no-untyped-def]
    return ctx.client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=ctx.auth(user),
        files={"file": (name, body, "application/pdf")},
    )


def test_direct_case_can_attach_pdf_and_link_evidence(ctx: Ctx) -> None:
    """신규 사건(점검 없이 시작)에서도 PDF 를 올리고 증거에 연결할 수 있다."""
    case_id = _new_case(ctx, ctx.user_a)
    r = _attach(ctx, ctx.user_a, case_id)
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    with ctx.factory() as s:
        doc = s.get(orm.DocumentModel, uuid.UUID(doc_id))
        assert doc is not None
        assert str(doc.case_id) == case_id and doc.check_session_id is None  # 부모는 사건 하나
        assert doc.owner_id == ctx.user_a and doc.retention_until == (NOW + timedelta(days=180)).date()
        log = s.scalars(select(orm.AuditLogModel).where(orm.AuditLogModel.target_id == doc.id)).one()
        assert log.action == "CREATE" and log.detail == {"size": len(PDF)}  # 파일명·내용 미기록
    assert len(list(ctx.files_dir.iterdir())) == 1

    r = ctx.client.post(
        f"/api/v1/cases/{case_id}/evidences",
        headers=ctx.auth(ctx.user_a),
        json={
            "evidence_type": "NOTICE",
            "title": "사업자 환불 거절 공문",
            "status": "HELD",
            "document_id": doc_id,
        },
    )
    assert r.status_code == 201, r.text


def test_cannot_attach_to_other_users_or_deleted_case(ctx: Ctx) -> None:
    case_a = _new_case(ctx, ctx.user_a)
    agree_all(ctx, ctx.user_b)
    assert _attach(ctx, ctx.user_b, case_a).status_code == 404  # 존재 여부 비노출
    assert ctx.client.delete(f"/api/v1/cases/{case_a}", headers=ctx.auth(ctx.user_a)).status_code == 204
    assert _attach(ctx, ctx.user_a, case_a).status_code == 404
    assert list(ctx.files_dir.iterdir()) == []  # 거부된 요청은 파일을 남기지 않음


def test_cannot_attach_to_closed_case(ctx: Ctx) -> None:
    case_id = _new_case(ctx, ctx.user_a)
    with ctx.factory() as s:
        row = s.get(orm.CaseModel, uuid.UUID(case_id))
        assert row is not None
        row.status = "CLOSED"
        s.commit()
    assert _attach(ctx, ctx.user_a, case_id).status_code == 409
    assert list(ctx.files_dir.iterdir()) == []


def test_same_upload_rules_as_session_upload(ctx: Ctx) -> None:
    """이미지·위장 파일은 받지 않는다(A안), 문서 동의 철회 시 차단."""
    case_id = _new_case(ctx, ctx.user_a)
    assert _attach(ctx, ctx.user_a, case_id, "evil.pdf", b"MZ\x90\x00").status_code == 422
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    r = ctx.client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers=ctx.auth(ctx.user_a),
        files={"file": ("문자캡처.png", png, "image/png")},
    )
    assert r.status_code == 422  # 증빙 이미지는 항목으로만 등록(A안)
    ctx.client.post(
        "/api/v1/consents",
        headers=ctx.auth(ctx.user_a),
        json={"consent_type": "SENSITIVE_DOCUMENT", "policy_version": "2026-11-v1", "agreed": False},
    )
    assert _attach(ctx, ctx.user_a, case_id).status_code == 428
    assert list(ctx.files_dir.iterdir()) == []


def test_attached_pdf_cannot_be_used_by_another_case(ctx: Ctx) -> None:
    case_1 = _new_case(ctx, ctx.user_a)
    doc_id = _attach(ctx, ctx.user_a, case_1).json()["id"]
    r = ctx.client.post(
        "/api/v1/cases",
        headers=ctx.auth(ctx.user_a),
        json={"title": "다른 사건", "case_type": "OTHER_CONSUMER"},
    )
    case_2 = r.json()["id"]
    r = ctx.client.post(
        f"/api/v1/cases/{case_2}/evidences",
        headers=ctx.auth(ctx.user_a),
        json={"evidence_type": "NOTICE", "title": "남의 사건 문서", "status": "HELD", "document_id": doc_id},
    )
    assert r.status_code == 422


def test_case_attachment_is_purged_with_case(ctx: Ctx) -> None:
    case_id = _new_case(ctx, ctx.user_a)
    _attach(ctx, ctx.user_a, case_id)
    assert ctx.client.delete(f"/api/v1/cases/{case_id}", headers=ctx.auth(ctx.user_a)).status_code == 204
    ctx.clock.current = NOW + timedelta(days=31)
    report = ctx.container.purge_expired_data().execute()
    assert report.cases == 1 and report.files == 1
    assert list(ctx.files_dir.iterdir()) == []
    with ctx.factory() as s:
        assert s.scalars(select(orm.DocumentModel)).all() == []
