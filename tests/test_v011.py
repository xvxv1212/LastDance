"""v0.11(보완 v1.4) 회귀 시험. 모두 합성 자료만 쓴다."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.infrastructure.db import models as orm
from app.infrastructure.knowledge import LocalKnowledgeBase
from app.interface.cli import accounts as cli_accounts
from tests.conftest import PASSWORD, Ctx, agree_all
from tests.test_change_requests import _approved, _count

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
        f"{API}/check-sessions",
        headers=h,
        json={"entry_type": "REFUND_CONCERN", "situation_text": "필라테스 6개월 계약 후 환불 문의"},
    ).json()["id"]
    r = ctx.client.post(
        f"{API}/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [{"fact_key": "PAYMENT_AMOUNT", "value_text": "600,000원", "source_kind": "STATEMENT"}]
        },
    )
    assert r.status_code == 201, r.text
    fid = r.json()["ids"][0]
    r = ctx.client.post(f"{API}/facts/{fid}/confirmation", headers=h, json={"action": "CONFIRM"})
    assert r.json()["session_status"] == "FACTS_CONFIRMED", r.text
    return uuid.UUID(sid), fid


@pytest.mark.parametrize(
    "summary", ["이 계약은 위법입니다.", "반드시 환불 대상입니다", "소송을 하세요", "승소 가능성이 높습니다"]
)
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


# ---------------------------------------------------------------------------
# UNKNOWN '모름 인지'(ACKNOWLEDGE_UNKNOWN) 계약
#   합성 사례: 필라테스 6개월 600,000원, 해지 요청일을 모른다
# ---------------------------------------------------------------------------
def _session_with_unknown(
    ctx: Ctx, user: uuid.UUID, known: int = 1, unknown: int = 1
) -> tuple[str, list[str], list[str]]:
    agree_all(ctx, user)
    sid = ctx.client.post(
        f"{API}/check-sessions",
        headers=ctx.auth(user),
        json={"entry_type": "REFUND_CONCERN", "situation_text": "필라테스 6개월 600,000원 결제 후 환불 문의"},
    ).json()["id"]
    known_keys = ["PAYMENT_AMOUNT", "PAYMENT_METHOD"][:known]
    unknown_keys = ["CANCEL_REQUEST_DATE", "CONTRACT_DATE"][:unknown]
    facts = [
        {
            "fact_key": k,
            "value_text": "600,000원" if k == "PAYMENT_AMOUNT" else "카드",
            "source_kind": "STATEMENT",
        }
        for k in known_keys
    ]
    facts += [{"fact_key": k, "source_kind": "UNKNOWN"} for k in unknown_keys]
    r = ctx.client.post(f"{API}/check-sessions/{sid}/facts", headers=ctx.svc(user), json={"facts": facts})
    assert r.status_code == 201, r.text
    ids = r.json()["ids"]
    return sid, ids[:known], ids[known:]


def _confirm(ctx: Ctx, user: uuid.UUID, fid: str, **body: object) -> Any:
    return ctx.client.post(f"{API}/facts/{fid}/confirmation", headers=ctx.auth(user), json=body)


def _fact_row(ctx: Ctx, fid: str) -> orm.ExtractedFactModel:
    with ctx.factory() as s:
        row: orm.ExtractedFactModel | None = s.get(orm.ExtractedFactModel, uuid.UUID(fid))
        assert row is not None
        s.expunge(row)
        return row


def _report(ctx: Ctx, user: uuid.UUID, sid: str, items: list[dict[str, Any]], **extra: object) -> Any:
    body: dict[str, object] = {
        "summary_text": "확인된 사실과 미확인 항목을 정리했습니다.",
        "items": items,
        **extra,
    }
    return ctx.client.post(f"{API}/check-sessions/{sid}/reports", headers=ctx.svc(user), json=body)


def test_ack_keeps_unknown_and_records_who_and_when(ctx: Ctx) -> None:
    _, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    assert _confirm(ctx, ctx.user_a, known[0], action="CONFIRM").json()["session_status"] == "FACTS_EXTRACTED"
    r = _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN")
    assert r.status_code == 200 and r.json()["session_status"] == "FACTS_CONFIRMED", r.text
    row = _fact_row(ctx, unknown[0])
    assert row.status == "ACKNOWLEDGED_UNKNOWN"
    assert row.source_kind == "UNKNOWN" and row.value_text is None and row.ai_value_text is None
    assert row.value_normalized is None  # 가짜 0·임의 날짜를 만들지 않는다
    assert row.confirmed_by == ctx.user_a and row.confirmed_at is not None


@pytest.mark.parametrize("bad", ["", "   ", "0", "모름", "2026-01-01"])
def test_ack_rejects_any_value(ctx: Ctx, bad: str) -> None:
    _, _, unknown = _session_with_unknown(ctx, ctx.user_a)
    r = _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN", new_value=bad)
    assert r.status_code == 422, r.text
    assert _fact_row(ctx, unknown[0]).status == "EXTRACTED"
    # null 은 '생략'과 같다
    assert (
        _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN", new_value=None).status_code == 200
    )


def test_ack_only_for_unknown_facts_and_owner(ctx: Ctx) -> None:
    _, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    assert _confirm(ctx, ctx.user_a, known[0], action="ACKNOWLEDGE_UNKNOWN").status_code == 422
    assert _fact_row(ctx, known[0]).status == "EXTRACTED"
    assert _confirm(ctx, ctx.user_b, unknown[0], action="ACKNOWLEDGE_UNKNOWN").status_code == 404
    # 미확인 항목을 '그대로 확인'하는 것은 여전히 안 된다(값이 없으므로)
    assert _confirm(ctx, ctx.user_a, unknown[0], action="CONFIRM").status_code == 422


def test_ack_all_unknown_cannot_reach_report(ctx: Ctx) -> None:
    sid, _, unknown = _session_with_unknown(ctx, ctx.user_a, known=0, unknown=2)
    for fid in unknown:
        r = _confirm(ctx, ctx.user_a, fid, action="ACKNOWLEDGE_UNKNOWN")
        assert r.status_code == 200 and r.json()["session_status"] == "FACTS_EXTRACTED"
    r = _report(
        ctx,
        ctx.user_a,
        sid,
        [{"category": "UNVERIFIED", "content": "해지 요청일 미확인", "fact_id": unknown[0]}],
    )
    assert r.status_code == 409, r.text
    assert _count(ctx, orm.InspectionReportModel) == 0


def test_ack_last_known_fact_rejected_returns_to_waiting(ctx: Ctx) -> None:
    sid, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    _confirm(ctx, ctx.user_a, known[0], action="CONFIRM")
    assert (
        _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN").json()["session_status"]
        == "FACTS_CONFIRMED"
    )
    draft = _report(ctx, ctx.user_a, sid, [
        {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": known[0]},
        {"category": "UNVERIFIED", "content": "해지 요청일 미확인", "fact_id": unknown[0]},
    ])  # fmt: skip
    assert draft.status_code == 201, draft.text
    r = _confirm(ctx, ctx.user_a, known[0], action="REJECT")
    assert r.status_code == 200 and r.json()["session_status"] == "FACTS_EXTRACTED"
    with ctx.factory() as s:
        row = s.get(orm.CheckSessionModel, uuid.UUID(sid))
        assert row is not None and row.facts_confirmed_at is None
    assert _fact_row(ctx, unknown[0]).status == "ACKNOWLEDGED_UNKNOWN"  # 자동 REJECT·자동 변경 없음
    # 되돌아간 상태에서는 예전 초안을 승인할 수 없고, 새 초안도 만들 수 없다
    assert (
        ctx.client.post(
            f"{API}/reports/{draft.json()['id']}/approval", headers=ctx.auth(ctx.user_a)
        ).status_code
        == 409
    )
    assert (
        _report(
            ctx, ctx.user_a, sid, [{"category": "UNVERIFIED", "content": "x", "fact_id": unknown[0]}]
        ).status_code
        == 409
    )
    # 값을 알게 되어 다시 입력하면 진행된다. 그래도 예전 초안은 낡은 초안이라 승인 불가
    ctx.clock.current += timedelta(minutes=1)
    assert (
        _confirm(ctx, ctx.user_a, known[0], action="MODIFY", new_value="550,000원").json()["session_status"]
        == "FACTS_CONFIRMED"
    )
    assert (
        ctx.client.post(
            f"{API}/reports/{draft.json()['id']}/approval", headers=ctx.auth(ctx.user_a)
        ).status_code
        == 409
    )


def test_ack_must_appear_in_report_as_unverified_with_fact_id(ctx: Ctx) -> None:
    sid, known, unknown = _session_with_unknown(ctx, ctx.user_a, known=1, unknown=2)
    _confirm(ctx, ctx.user_a, known[0], action="CONFIRM")
    for fid in unknown:
        _confirm(ctx, ctx.user_a, fid, action="ACKNOWLEDGE_UNKNOWN")
    ok_fact = {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": known[0]}
    unv = [
        {"category": "UNVERIFIED", "content": f"미확인 항목 {n}", "fact_id": fid}
        for n, fid in enumerate(unknown)
    ]
    # ① 하나라도 빠지면 422
    assert _report(ctx, ctx.user_a, sid, [ok_fact, unv[0]]).status_code == 422
    # ② fact_id 없이 문장만 쓰면 '연결'이 아니므로 422
    assert (
        _report(
            ctx, ctx.user_a, sid, [ok_fact, unv[0], {"category": "UNVERIFIED", "content": "미확인 항목 1"}]
        ).status_code
        == 422
    )
    # ③ '확인된 사실'·'주의'에 연결하면 422
    for cat in ("CONFIRMED_FACT", "CAUTION"):
        bad = {"category": cat, "content": "해지 요청일", "fact_id": unknown[1]}
        assert _report(ctx, ctx.user_a, sid, [ok_fact, *unv, bad]).status_code == 422, cat
    # ④ 다른 점검(다른 사용자·같은 사용자)의 사실 ID 를 가리키면 422
    _, other_known, _ = _session_with_unknown(ctx, ctx.user_b)
    _, own_other, _ = _session_with_unknown(ctx, ctx.user_a)
    for foreign in (other_known[0], own_other[0], str(uuid.uuid4())):
        bad = {"category": "UNVERIFIED", "content": "다른 점검 항목", "fact_id": foreign}
        assert _report(ctx, ctx.user_a, sid, [ok_fact, *unv, bad]).status_code == 422
    assert _count(ctx, orm.InspectionReportModel) == 0
    # ⑤ 전부 들어 있으면 201
    assert _report(ctx, ctx.user_a, sid, [ok_fact, *unv]).status_code == 201


def test_ack_not_in_snapshot_or_timeline_but_kept_as_unverified_on_conversion(ctx: Ctx) -> None:
    sid, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    _confirm(ctx, ctx.user_a, known[0], action="CONFIRM")
    _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN")  # CANCEL_REQUEST_DATE(날짜형)
    rid = _report(ctx, ctx.user_a, sid, [
        {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": known[0]},
        {"category": "UNVERIFIED", "content": "해지 요청일을 아직 모릅니다.", "fact_id": unknown[0]},
    ]).json()["id"]  # fmt: skip
    assert ctx.client.post(f"{API}/reports/{rid}/approval", headers=h).status_code == 204
    r = ctx.client.post(
        f"{API}/check-sessions/{sid}/case-conversion", headers=h, json={"title": "필라테스 환불"}
    )
    assert r.status_code == 201, r.text
    d = ctx.client.get(f"{API}/cases/{r.json()['id']}", headers=h).json()
    snap_ids = {f["fact_id"] for f in d["snapshot"]["facts"]}
    assert snap_ids == {known[0]} and unknown[0] not in snap_ids
    assert all(f["value"] is not None for f in d["snapshot"]["facts"])
    assert d["events"] == []  # 모르는 날짜로 타임라인을 만들지 않는다
    assert d["unverified"] == ["해지 요청일을 아직 모릅니다."]
    assert _fact_row(ctx, unknown[0]).status == "ACKNOWLEDGED_UNKNOWN"


def test_ack_then_user_learns_value(ctx: Ctx) -> None:
    """모름 처리 뒤 값을 알게 되면 MODIFY 로 입력한다(근거 = 진술). 초안은 다시 만들어야 한다."""
    _, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    _confirm(ctx, ctx.user_a, known[0], action="CONFIRM")
    _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN")
    r = _confirm(ctx, ctx.user_a, unknown[0], action="MODIFY", new_value="2026-05-10")
    assert r.status_code == 200
    row = _fact_row(ctx, unknown[0])
    assert (row.status, row.source_kind, row.value_text, row.ai_value_text) == (
        "MODIFIED",
        "STATEMENT",
        "2026-05-10",
        None,
    )


def test_db_check_blocks_ack_on_known_fact(ctx: Ctx) -> None:
    """앱을 거치지 않고 DB 에 직접 써도 값 있는 사실에는 '모름' 상태를 붙일 수 없다."""
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError

    _, known, _ = _session_with_unknown(ctx, ctx.user_a)
    with ctx.factory() as s, pytest.raises(IntegrityError):
        s.execute(
            update(orm.ExtractedFactModel)
            .where(orm.ExtractedFactModel.id == uuid.UUID(known[0]))
            .values(status="ACKNOWLEDGED_UNKNOWN", confirmed_by=ctx.user_a, confirmed_at=ctx.clock.now())
        )
        s.commit()


# ---------------------------------------------------------------------------
# 낡은 초안 차단(생성 중 수정 포함)
# ---------------------------------------------------------------------------
def test_draft_based_on_older_facts_is_rejected_when_basis_is_sent(ctx: Ctx) -> None:
    sid, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    _confirm(ctx, ctx.user_a, known[0], action="CONFIRM")
    _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN")
    basis = ctx.client.get(f"{API}/check-sessions/{sid}/facts", headers=h).json()["facts_confirmed_at"]
    assert basis is not None
    items = [
        {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": known[0]},
        {"category": "UNVERIFIED", "content": "해지 요청일 미확인", "fact_id": unknown[0]},
    ]
    # AI 가 초안을 만드는 사이 사용자가 금액을 고쳤다
    ctx.clock.current += timedelta(minutes=2)
    assert _confirm(ctx, ctx.user_a, known[0], action="MODIFY", new_value="550,000원").status_code == 200
    ctx.clock.current += timedelta(minutes=1)
    assert _report(ctx, ctx.user_a, sid, items, facts_confirmed_at=basis).status_code == 409
    assert _count(ctx, orm.InspectionReportModel) == 0
    # 최신 기준으로 다시 읽으면 통과
    fresh = ctx.client.get(f"{API}/check-sessions/{sid}/facts", headers=h).json()["facts_confirmed_at"]
    assert fresh != basis
    assert _report(ctx, ctx.user_a, sid, items, facts_confirmed_at=fresh).status_code == 201
    # 기준을 보내지 않는 기존 호출은 그대로 동작(호환)
    assert _report(ctx, ctx.user_a, sid, items).status_code == 201


# ---------------------------------------------------------------------------
# 조회 API(F-04): 사실 목록·보고서 상세
# ---------------------------------------------------------------------------
def test_get_facts_shows_ai_value_current_value_status_and_source(ctx: Ctx) -> None:
    sid, known, unknown = _session_with_unknown(ctx, ctx.user_a, known=2, unknown=1)
    h = ctx.auth(ctx.user_a)
    before = _count(ctx, orm.AuditLogModel, action="READ_SENSITIVE")
    _confirm(ctx, ctx.user_a, known[0], action="MODIFY", new_value="550,000원")
    _confirm(ctx, ctx.user_a, known[1], action="CONFIRM")
    _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN")
    r = ctx.client.get(f"{API}/check-sessions/{sid}/facts", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_status"] == "FACTS_CONFIRMED" and body["facts_confirmed_at"]
    by_id = {f["id"]: f for f in body["facts"]}
    assert set(by_id) == set(known + unknown)
    m = by_id[known[0]]
    assert (m["ai_value"], m["current_value"], m["status"]) == ("600,000원", "550,000원", "MODIFIED")
    assert m["confirmed_at"] is not None and m["source_kind"] == "STATEMENT"
    u = by_id[unknown[0]]
    assert (u["ai_value"], u["current_value"], u["status"], u["source_kind"]) == (
        None,
        None,
        "ACKNOWLEDGED_UNKNOWN",
        "UNKNOWN",
    )
    text = r.text
    for banned in ("confidence", "storage_key", "password", "thread_id", "confirmed_by"):
        assert banned not in text
    assert _count(ctx, orm.AuditLogModel, action="READ_SENSITIVE") == before + 1


def test_get_facts_isolation_and_auth(ctx: Ctx) -> None:
    sid, _, _ = _session_with_unknown(ctx, ctx.user_a)
    url = f"{API}/check-sessions/{sid}/facts"
    assert ctx.client.get(url).status_code == 401
    assert ctx.client.get(url, headers=ctx.auth(ctx.user_b)).status_code == 404
    assert (
        ctx.client.get(f"{API}/check-sessions/{uuid.uuid4()}/facts", headers=ctx.auth(ctx.user_a)).status_code
        == 404
    )
    # AI 서비스 토큰(쓰기 scope)으로는 읽을 수 없다 — 쓰기 권한이 읽기를 자동 허용하지 않는다
    assert ctx.client.get(url, headers=ctx.svc(ctx.user_a)).status_code == 401
    # 기존 POST 계약은 그대로(같은 경로의 POST 는 서비스 토큰 전용)
    assert ctx.client.post(url, headers=ctx.auth(ctx.user_a), json={"facts": []}).status_code in (403, 422)


def test_get_report_detail_version_citation_copy_hold_reason(ctx: Ctx) -> None:
    sid, known, unknown = _session_with_unknown(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    _confirm(ctx, ctx.user_a, known[0], action="CONFIRM")
    _confirm(ctx, ctx.user_a, unknown[0], action="ACKNOWLEDGE_UNKNOWN")
    items: list[dict[str, Any]] = [
        {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": known[0]},
        {
            "category": "CAUTION",
            "content": "환급 기준 비교 필요",
            "citations": [{"source_chunk_id": ctx.chunk_id, "quoted_text": "환급 기준"}],
        },
        {
            "category": "CAUTION",
            "content": "출처 재확인 필요",
            "citations": [{"source_chunk_id": "no-such#9", "quoted_text": "없는 문장"}],
        },
        {"category": "CAUTION", "content": "근거 미첨부 항목"},
        {"category": "UNVERIFIED", "content": "해지 요청일 미확인", "fact_id": unknown[0]},
    ]
    post = _report(ctx, ctx.user_a, sid, items)
    assert post.status_code == 201 and set(post.json()) == {"id", "held_items"}  # 기존 POST 응답 계약 유지
    rid = post.json()["id"]
    before = _count(ctx, orm.AuditLogModel, action="READ_SENSITIVE")
    r = ctx.client.get(f"{API}/reports/{rid}", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["version"], d["status"], d["is_stale"], d["held_items"], d["approved_at"]) == (
        1,
        "DRAFT",
        False,
        2,
        None,
    )
    got = d["items"]
    assert [i["category"] for i in got] == [i["category"] for i in items]
    ok = got[1]["citations"][0]
    assert (ok["check_result"], ok["chunk_id"], ok["article_ref"]) == ("VERIFIED", ctx.chunk_id, "별표 2")
    assert ok["source_title"] and ok["source_url"] and ok["effective_date"] == "2025-01-01"
    fake = got[2]["citations"][0]
    assert (fake["check_result"], fake["chunk_id"], fake["requested_chunk_ref"]) == (
        "CHUNK_NOT_FOUND",
        None,
        "no-such#9",
    )
    assert [i["hold_reason"] for i in got] == [None, None, "CITATION_FAILED", "NO_CITATION", None]
    assert got[4]["fact_id"] == unknown[0]
    assert "confidence" not in r.text and "storage_key" not in r.text
    assert _count(ctx, orm.AuditLogModel, action="READ_SENSITIVE") == before + 1
    # 격리·인증
    assert ctx.client.get(f"{API}/reports/{rid}").status_code == 401
    assert ctx.client.get(f"{API}/reports/{rid}", headers=ctx.auth(ctx.user_b)).status_code == 404
    assert ctx.client.get(f"{API}/reports/{rid}", headers=ctx.svc(ctx.user_a)).status_code == 401
    assert ctx.client.get(f"{API}/reports/{uuid.uuid4()}", headers=h).status_code == 404
    # 사실을 고치면 이 초안은 낡은 초안으로 표시되고 승인되지 않는다
    ctx.clock.current += timedelta(minutes=1)
    _confirm(ctx, ctx.user_a, known[0], action="MODIFY", new_value="550,000원")
    assert ctx.client.get(f"{API}/reports/{rid}", headers=h).json()["is_stale"] is True
    assert ctx.client.post(f"{API}/reports/{rid}/approval", headers=h).status_code == 409


def test_approved_report_text_is_unchanged_after_later_reads(ctx: Ctx) -> None:
    """승인 원문 불변: 승인본은 조회해도, 이후 지식 DB 가 바뀌어도 인용 당시 사본 그대로다."""
    sid, rid, _ = _approved(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    first = ctx.client.get(f"{API}/reports/{rid}", headers=h).json()
    assert first["status"] == "APPROVED" and first["approved_at"] and first["is_stale"] is False
    ctx.container.knowledge = LocalKnowledgeBase([], [])  # 지식 DB 가 비워진 상황
    assert ctx.client.get(f"{API}/reports/{rid}", headers=h).json() == first
    # 승인 뒤 사실 수정은 계속 409(정정은 새 버전 절차 — 이번 범위는 제안만)
    with ctx.factory() as s:
        fid = s.scalars(
            select(orm.ExtractedFactModel.id).where(orm.ExtractedFactModel.check_session_id == uuid.UUID(sid))
        ).first()
    assert _confirm(ctx, ctx.user_a, str(fid), action="MODIFY", new_value="1원").status_code == 409


# ---------------------------------------------------------------------------
# RAG: 임베딩 설정(spec) 누락 차단 · 설정 지문(manifest 해시)
# ---------------------------------------------------------------------------
def test_rag_spec_is_required_for_vector_load_and_search() -> None:
    from sqlalchemy import create_engine

    from app.infrastructure.knowledge import KnowledgeChunk, KnowledgeSource
    from app.infrastructure.pgvector_knowledge import PgVectorKnowledgeBase, load_knowledge

    engine = create_engine("sqlite://")  # 검사는 DB 에 닿기 전에 끝난다
    src = KnowledgeSource(
        id="s", source_type="LAW", title="합성", issuer="합성", url="https://example.test/", version_label="t"
    )
    chunk = KnowledgeChunk(id="s#0", source_id="s", chunk_index=0, article_ref=None, content="합성 본문")
    with pytest.raises(ValueError, match="spec"):
        load_knowledge(engine, [src], [chunk], {"s#0": [0.0] * 384})
    with pytest.raises(ValueError, match="spec"):
        load_knowledge(engine, [src], [chunk], embedding_model="intfloat/multilingual-e5-small")
    with pytest.raises(ValueError, match="spec"):
        PgVectorKnowledgeBase(engine).similar_chunks([0.0] * 384)


def test_rag_candidate_spec_unchanged_and_settings_hash_separates_indexes() -> None:
    import dataclasses

    from app.infrastructure.pgvector_knowledge import E5_SMALL_CANDIDATE as E5

    # 후보 설정은 v0.10 과 같다(모델·고정 리비전·384차원·접두어·평균 풀링·L2·512 토큰)
    assert E5.identifier == "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert (E5.dimension, E5.pooling, E5.l2_normalize, E5.max_tokens) == (384, "mean", True, 512)
    assert E5.format_query("환불") == "query: 환불" and E5.format_passage("기준") == "passage: 기준"
    with pytest.raises(ValueError):
        E5.validate([float("inf")] + [0.0] * 383)
    a = dataclasses.replace(E5, preprocessing="nfc", splitter="article/800/100", corpus_version="m1")
    b = dataclasses.replace(a, corpus_version="m2")
    c = dataclasses.replace(a, splitter="article/400/50")
    ids = {E5.identifier, a.identifier, b.identifier, c.identifier}
    assert len(ids) == 4 and all(len(i) <= 100 for i in ids)  # embedding_model VARCHAR(100) 안에 들어간다
    assert (
        a.identifier
        == dataclasses.replace(
            E5, preprocessing="nfc", splitter="article/800/100", corpus_version="m1"
        ).identifier
    )


# ---------------------------------------------------------------------------
# 팀장 격리 UNKNOWN 패치의 SYN-001 관찰과 같은 조건(관찰값 대조용, 합성)
#   알려진 4사실(대역 6회 → 진술 4회 수정 포함) + ACK 3건 + 근거 미첨부 보류 1건
#   팀장 보고 기대: 초안 held_items=1, 전환 Snapshot 4, 미확인 4(ACK 3 + 보류 1), 날짜 이벤트 0
# ---------------------------------------------------------------------------
def test_syn001_text_flow_matches_lead_patch_observation(ctx: Ctx) -> None:
    agree_all(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    sid = ctx.client.post(
        f"{API}/check-sessions",
        headers=h,
        json={
            "entry_type": "REFUND_CONCERN",
            "situation_text": "필라테스 6개월 600,000원, 4회 이용 후 환불 문의(합성)",
        },
    ).json()["id"]
    facts = [
        {"fact_key": "BUSINESS_NAME", "value_text": "필라테스(합성)", "source_kind": "STATEMENT"},
        {"fact_key": "CONTRACT_PERIOD", "value_text": "6개월", "source_kind": "STATEMENT"},
        {"fact_key": "PAYMENT_AMOUNT", "value_text": "600,000원", "source_kind": "STATEMENT"},
        {"fact_key": "USED_SESSIONS", "value_text": "6회", "source_kind": "STATEMENT"},  # 의도적 대역 오류
        {"fact_key": "CONTRACT_DATE", "source_kind": "UNKNOWN"},
        {"fact_key": "CANCEL_REQUEST_DATE", "source_kind": "UNKNOWN"},
        {"fact_key": "OTHER", "source_kind": "UNKNOWN"},  # 계약서 사본 보유 여부(PDF 미제출 ≠ 사본 없음)
    ]
    ids = ctx.client.post(
        f"{API}/check-sessions/{sid}/facts", headers=ctx.svc(ctx.user_a), json={"facts": facts}
    ).json()["ids"]
    known, acks = ids[:4], ids[4:]
    for fid in known[:3]:
        assert _confirm(ctx, ctx.user_a, fid, action="CONFIRM").status_code == 200
    assert _confirm(ctx, ctx.user_a, known[3], action="MODIFY", new_value="4회").status_code == 200
    statuses = [
        _confirm(ctx, ctx.user_a, fid, action="ACKNOWLEDGE_UNKNOWN").json()["session_status"] for fid in acks
    ]
    assert statuses == ["FACTS_EXTRACTED", "FACTS_EXTRACTED", "FACTS_CONFIRMED"]
    items: list[dict[str, Any]] = [
        {"category": "CONFIRMED_FACT", "content": c, "fact_id": f}
        for c, f in zip(
            ["필라테스(합성)", "계약기간 6개월", "결제 600,000원", "이용 4회"], known, strict=True
        )
    ]
    items += [
        {"category": "UNVERIFIED", "content": c, "fact_id": f}
        for c, f in zip(
            ["계약일 미확인", "해지 요청일 미확인", "계약서 사본 보유 여부 미확인"], acks, strict=True
        )
    ]
    items.append(
        {"category": "CAUTION", "content": "환급 기준은 공식 근거 확인 후 안내합니다."}
    )  # NO_CITATION 보류
    r = _report(ctx, ctx.user_a, sid, items)
    assert r.status_code == 201 and r.json()["held_items"] == 1, r.text
    assert ctx.client.post(f"{API}/reports/{r.json()['id']}/approval", headers=h).status_code == 204
    c = ctx.client.post(
        f"{API}/check-sessions/{sid}/case-conversion", headers=h, json={"title": "SYN-001(합성)"}
    )
    d = ctx.client.get(f"{API}/cases/{c.json()['id']}", headers=h).json()
    snap = d["snapshot"]["facts"]
    assert len(snap) == 4 and {f["fact_id"] for f in snap} == set(known)
    assert next(f for f in snap if f["fact_id"] == known[3])["value"] == "4회"
    assert len(d["unverified"]) == 4 and d["events"] == []
    for fid in acks:
        row = _fact_row(ctx, fid)
        got = (row.status, row.value_text, row.ai_value_text, row.document_id, row.page_no, row.source_span)
        assert got == ("ACKNOWLEDGED_UNKNOWN", None, None, None, None, None)
