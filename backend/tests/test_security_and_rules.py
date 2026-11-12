"""보안·업무 규칙 테스트(기획서 품질 기준 '권한 우회 0건', '가짜 출처 0건', '금지 법률 판단 0건')."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.domain.policies import LegalExpressionPolicy, PersonalInfoMasker
from tests.conftest import SECRET, Ctx, agree_all


def _new_session(ctx: Ctx, user: uuid.UUID) -> str:
    r = ctx.client.post(
        "/api/v1/check-sessions",
        headers=ctx.auth(user),
        json={"entry_type": "DISPUTE_OCCURRED", "situation_text": "헬스장 환불 거절"},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# --- 인증 -------------------------------------------------------------------
def test_requires_token(ctx: Ctx) -> None:
    assert (
        ctx.client.post("/api/v1/cases", json={"title": "x", "case_type": "OTHER_CONSUMER"}).status_code
        == 401
    )


def test_rejects_alg_none_token(ctx: Ctx) -> None:
    forged = jwt.encode(
        {"sub": str(ctx.user_a), "iss": "gwonri-chaebi", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        key="",
        algorithm="none",
    )
    r = ctx.client.get(f"/api/v1/cases/{uuid.uuid4()}", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_rejects_expired_token(ctx: Ctx) -> None:
    expired = jwt.encode(
        {"sub": str(ctx.user_a), "iss": "gwonri-chaebi", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        SECRET,
        algorithm="HS256",
    )
    r = ctx.client.get(f"/api/v1/cases/{uuid.uuid4()}", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


# --- 권한(IDOR) -------------------------------------------------------------
def test_other_users_case_is_hidden(ctx: Ctx) -> None:
    r = ctx.client.post(
        "/api/v1/cases",
        headers=ctx.auth(ctx.user_a),
        json={"title": "A의 사건", "case_type": "FITNESS_REFUND"},
    )
    case_id = r.json()["id"]
    # B 가 A 의 사건을 조회·삭제·증거등록 시도 → 모두 404(존재 자체를 숨김)
    hb = ctx.auth(ctx.user_b)
    assert ctx.client.get(f"/api/v1/cases/{case_id}", headers=hb).status_code == 404
    assert ctx.client.delete(f"/api/v1/cases/{case_id}", headers=hb).status_code == 404
    assert (
        ctx.client.post(
            f"/api/v1/cases/{case_id}/evidences",
            headers=hb,
            json={"evidence_type": "OTHER", "title": "x", "status": "MISSING"},
        ).status_code
        == 404
    )


def test_deleted_case_is_not_readable(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    case_id = ctx.client.post(
        "/api/v1/cases", headers=h, json={"title": "삭제 테스트", "case_type": "OTHER_CONSUMER"}
    ).json()["id"]
    assert ctx.client.delete(f"/api/v1/cases/{case_id}", headers=h).status_code == 204
    assert ctx.client.get(f"/api/v1/cases/{case_id}", headers=h).status_code == 404


# --- 동의 -------------------------------------------------------------------
def test_consent_required_before_session(ctx: Ctx) -> None:
    r = ctx.client.post(
        "/api/v1/check-sessions",
        headers=ctx.auth(ctx.user_a),
        json={"entry_type": "PRE_CONTRACT", "situation_text": "계약 전 확인하고 싶어요"},
    )
    assert r.status_code == 428


def test_withdrawn_consent_blocks_upload(ctx: Ctx) -> None:
    agree_all(ctx, ctx.user_a)
    sid = _new_session(ctx, ctx.user_a)
    ctx.client.post(
        "/api/v1/consents",
        headers=ctx.auth(ctx.user_a),
        json={"consent_type": "SENSITIVE_DOCUMENT", "policy_version": "2026-11-v1", "agreed": False},
    )
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/documents",
        headers=ctx.auth(ctx.user_a),
        files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 428


# --- 업로드 -----------------------------------------------------------------
def test_upload_rejects_fake_pdf_and_path_names(ctx: Ctx) -> None:
    agree_all(ctx, ctx.user_a)
    sid = _new_session(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    fake = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/documents",
        headers=h,
        files={"file": ("evil.pdf", b"MZ\x90\x00", "application/pdf")},
    )
    assert fake.status_code == 422
    ok = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/documents",
        headers=h,
        files={"file": ("../../etc/passwd.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert ok.status_code == 201


def test_extra_fields_rejected(ctx: Ctx) -> None:
    """정의되지 않은 필드(owner_id 등) 주입 차단(Mass Assignment)."""
    r = ctx.client.post(
        "/api/v1/cases",
        headers=ctx.auth(ctx.user_a),
        json={"title": "주입", "case_type": "OTHER_CONSUMER", "owner_id": str(ctx.user_b)},
    )
    assert r.status_code == 422


# --- 보고서 규칙 ------------------------------------------------------------
def _confirmed_session(ctx: Ctx) -> tuple[str, str]:
    agree_all(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    sid = _new_session(ctx, ctx.user_a)
    fid = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={"facts": [{"fact_key": "BUSINESS_NAME", "value_text": "XX헬스", "source_kind": "STATEMENT"}]},
    ).json()["ids"][0]
    ctx.client.post(f"/api/v1/facts/{fid}/confirmation", headers=h, json={"action": "CONFIRM"})
    return sid, fid


def test_report_holds_fake_citation(ctx: Ctx) -> None:
    """가짜 출처·클라이언트 '검증됨' 주장은 통하지 않는다 → 해당 항목은 보류(AI-03)."""
    sid, _ = _confirmed_session(ctx)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "요약",
            "items": [
                {
                    "category": "CAUTION",
                    "content": "확인 필요",
                    "citations": [{"source_chunk_id": str(uuid.uuid4()), "quoted_text": "가짜"}],
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["held_items"] == 1


def test_client_cannot_claim_verified(ctx: Ctx) -> None:
    """요청에 is_verified 를 넣으면 스키마가 거부한다(서버만 판정)."""
    sid, _ = _confirmed_session(ctx)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "요약",
            "items": [
                {
                    "category": "CAUTION",
                    "content": "확인 필요",
                    "citations": [
                        {
                            "source_chunk_id": str(ctx.chunk_id),
                            "quoted_text": "환급 기준",
                            "is_verified": True,
                        }
                    ],
                }
            ],
        },
    )
    assert r.status_code == 422


def test_report_rejects_legal_conclusion(ctx: Ctx) -> None:
    sid, fid = _confirmed_session(ctx)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "요약",
            "items": [
                {
                    "category": "CONFIRMED_FACT",
                    "fact_id": fid,
                    "content": "사업자의 행위는 위법입니다. 소송하면 승소합니다.",
                }
            ],
        },
    )
    assert r.status_code == 422


def test_report_requires_confirmed_fact_link(ctx: Ctx) -> None:
    sid, _ = _confirmed_session(ctx)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={"summary_text": "요약", "items": [{"category": "CONFIRMED_FACT", "content": "근거 없는 사실"}]},
    )
    assert r.status_code == 422


def test_cannot_convert_without_approved_report(ctx: Ctx) -> None:
    sid, _ = _confirmed_session(ctx)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/case-conversion",
        headers=ctx.auth(ctx.user_a),
        json={"title": "보고서 없이 전환"},
    )
    assert r.status_code == 409


# --- 순수 정책 단위 테스트 --------------------------------------------------
def test_masker() -> None:
    m = PersonalInfoMasker()
    out = m.mask("주민번호 900101-1234567, 카드 1234-5678-9012-3456")
    assert "900101" not in out and "1234-5678-9012-3456" not in out


def test_legal_policy_allows_neutral_text() -> None:
    assert LegalExpressionPolicy().violations("환급 기준을 확인해 보세요.") == []


# --- 외부 API 전송 처리 근거(권리채비 기획서 InputGuard) ------------------------------
def test_external_transfer_requires_lawful_basis() -> None:
    """외부 API 로 보낸 기록에 처리 근거가 없으면 기록 자체를 만들 수 없다.

    v0.6: 구 agent_runs 의 DB CHECK 2개를 AiCallRecord 생성 검사로 옮겼다(감사 로그 JSON 에 저장).
    """
    import pytest

    from app.domain import enums
    from app.domain.entities import AiCallRecord
    from app.domain.errors import ValidationError

    ok = AiCallRecord("N1", enums.AgentRunStatus.SUCCEEDED, True, enums.TransferBasis.CONSENT)
    assert ok.to_detail()["transfer_basis"] == "CONSENT"
    with pytest.raises(ValidationError):
        AiCallRecord("N1", enums.AgentRunStatus.SUCCEEDED, True, enums.TransferBasis.NOT_APPLICABLE)
    with pytest.raises(ValidationError):  # 차단인데 외부 전송함
        AiCallRecord("N0", enums.AgentRunStatus.BLOCKED, True, enums.TransferBasis.CONSENT, "SENSITIVE_DATA")
    with pytest.raises(ValidationError):  # 차단인데 사유 코드 없음
        AiCallRecord("N0", enums.AgentRunStatus.BLOCKED, False)
