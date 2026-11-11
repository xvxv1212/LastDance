"""v0.11(보완 v1.4) 회귀 시험. 모두 합성 자료만 쓴다."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.infrastructure.db import models as orm
from app.interface.cli import accounts as cli_accounts
from tests.conftest import PASSWORD, Ctx, agree_all
from tests.test_change_requests import _approved, _count  # noqa: F401 - 뒤 시험에서 사용

API = "/api/v1"


# ---------------------------------------------------------------------------
# SUP-01 공개 가입 기본 닫힘 + 권한 있는 시험 계정 발급
# ---------------------------------------------------------------------------
def _signup(ctx: Ctx, email: str) -> int:
    r = ctx.client.post(f"{API}/auth/signup", json={"email": email, "password": PASSWORD})
    return r.status_code


def test_sup01_default_setting_is_closed() -> None:
    """설정 기본값: 환경변수가 없으면 공개 가입은 닫힘. 모르는 값도 닫힘으로 본다."""
    from app.config import Settings, _flag

    assert Settings(database_url="x", jwt_secret="y" * 40, storage_dir=None).public_signup_enabled is False  # type: ignore[arg-type]
    assert [_flag(v) for v in (None, "", "0", "false", "maybe", "TRUE", "1", "on")] == [
        False, False, False, False, False, True, True, True,
    ]  # fmt: skip


def test_sup01_signup_rejected_when_closed(ctx: Ctx) -> None:
    ctx.container.public_signup_enabled = False
    before = _count(ctx, orm.UserModel)
    assert _signup(ctx, "closed@example.com") == 403
    # 닫힌 상태에서는 '이미 있는 이메일'도 같은 403 → 가입 여부를 알려 주지 않는다
    assert _signup(ctx, "a@example.com") == 403
    assert _count(ctx, orm.UserModel) == before


def test_sup01_issued_account_can_login_while_signup_closed(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx.container.public_signup_enabled = False
    monkeypatch.setenv(cli_accounts.PASSWORD_ENV, PASSWORD)
    assert cli_accounts.main(["--email", "issued@example.com"], ctx.container) == 0
    out = capsys.readouterr()
    assert PASSWORD not in out.out + out.err  # 비밀번호는 어디에도 출력하지 않는다
    r = ctx.client.post(f"{API}/auth/login", json={"email": "issued@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["access_token"]
    # 같은 이메일 재발급은 실패(사유 코드만), 약한 비밀번호도 실패
    assert cli_accounts.main(["--email", "issued@example.com"], ctx.container) == 1
    monkeypatch.setenv(cli_accounts.PASSWORD_ENV, "short")
    assert cli_accounts.main(["--email", "weak@example.com"], ctx.container) == 1
    monkeypatch.delenv(cli_accounts.PASSWORD_ENV)
    assert cli_accounts.main(["--email", "nopass@example.com"], ctx.container) == 2


def test_sup01_signup_works_only_when_explicitly_enabled(ctx: Ctx) -> None:
    assert ctx.container.public_signup_enabled is True  # conftest 가 명시적으로 켠 상태
    assert _signup(ctx, "open@example.com") == 201


# ---------------------------------------------------------------------------
# SUP-02 요약문 법률 단정 표현 차단
# ---------------------------------------------------------------------------
def _confirmed_session(ctx: Ctx) -> tuple[uuid.UUID, str]:
    agree_all(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    sid = ctx.client.post(
        f"{API}/check-sessions", headers=h, json={"entry_type": "REFUND_CONCERN", "situation_text": "필라테스 6개월 계약 후 환불 문의"}
    ).json()["id"]
    r = ctx.client.post(
        f"{API}/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={"facts": [{"fact_key": "PAYMENT_AMOUNT", "value_text": "600,000원", "source_kind": "STATEMENT"}]},
    )
    assert r.status_code == 201, r.text
    fid = r.json()["ids"][0]
    r = ctx.client.post(f"{API}/facts/{fid}/confirmation", headers=h, json={"action": "CONFIRM"})
    assert r.json()["session_status"] == "FACTS_CONFIRMED", r.text
    return uuid.UUID(sid), fid


@pytest.mark.parametrize("summary", ["이 계약은 위법입니다.", "반드시 환불 대상입니다", "소송을 하세요", "승소 가능성이 높습니다"])
def test_sup02_summary_only_prohibited_expression_rejected(ctx: Ctx, summary: str) -> None:
    sid, fid = _confirmed_session(ctx)
    body = {
        "summary_text": summary,
        "items": [{"category": "CONFIRMED_FACT", "content": "결제 금액 600,000원", "fact_id": fid}],
    }
    r = ctx.client.post(f"{API}/check-sessions/{sid}/reports", headers=ctx.svc(ctx.user_a), json=body)
    assert r.status_code == 422, r.text
    assert _count(ctx, orm.InspectionReportModel) == 0  # 초안 자체가 저장되지 않는다
    body["summary_text"] = "확인된 사실과 추가 확인이 필요한 항목을 정리했습니다."
    r = ctx.client.post(f"{API}/check-sessions/{sid}/reports", headers=ctx.svc(ctx.user_a), json=body)
    assert r.status_code == 201, r.text
