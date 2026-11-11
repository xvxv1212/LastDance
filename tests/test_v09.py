"""v0.9 (2026.11.10 MVP 결정 5개) 우선 검증 테스트.

검토 문서가 '우선 검증'으로 지정한 네 가지를 각각 묶었다.
  1. 회원 탈퇴(+이메일 가입·로그인)      2. 미전환 점검 파기(+종결 사건·감사 로그 보관 기간)
  3. 사용자별 접근 제한             4. AI 내부 API 권한 분리(R-01)
SQLite·PostgreSQL·RLS 모드(HJ_TEST_DATABASE_URL / HJ_TEST_APP_DATABASE_URL)에서 같은 테스트가 돈다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select, update

from app.domain import enums
from app.infrastructure.db import models as orm
from app.interface.api.dependencies import SCOPE_FACTS_WRITE, SCOPE_REPORTS_WRITE
from tests.conftest import NOW, PASSWORD, SECRET, SERVICE_SECRET, Ctx, agree_all
from tests.test_change_requests import _approved, _convert, _count, _session, _thread, _upload


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------
def _signup(ctx: Ctx, email: str, password: str = PASSWORD) -> int:
    r = ctx.client.post("/api/v1/auth/signup", json={"email": email, "password": password})
    return r.status_code


def _login(ctx: Ctx, email: str, password: str = PASSWORD) -> tuple[int, str | None, dict[str, str]]:
    """(상태코드, 사용자 ID, 인증 헤더). 로그인 토큰의 iat·exp 는 서버 시계 기준이므로 실제 시각에 맞춘다."""
    ctx.clock.current = datetime.now(UTC)
    r = ctx.client.post("/api/v1/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        return r.status_code, None, {}
    tok = r.json()["access_token"]
    uid = str(jwt.decode(tok, options={"verify_signature": False})["sub"])
    return 200, uid, {"Authorization": f"Bearer {tok}"}


def _withdraw(ctx: Ctx, headers: dict[str, str], password: str = PASSWORD) -> int:
    return ctx.client.post("/api/v1/me/withdrawal", headers=headers, json={"password": password}).status_code


def _user_row(ctx: Ctx, user_id: uuid.UUID) -> orm.UserModel | None:
    with ctx.factory() as s:
        row = s.get(orm.UserModel, user_id)
        if row is not None:
            s.expunge(row)
        return row


def _owned_rows(ctx: Ctx, user: uuid.UUID) -> dict[str, int]:
    return {
        "sessions": _count(ctx, orm.CheckSessionModel, user_id=user),
        "cases": _count(ctx, orm.CaseModel, owner_id=user),
        "documents": _count(ctx, orm.DocumentModel, owner_id=user),
        "consents": _count(ctx, orm.UserConsentModel, user_id=user),
        "facts": _facts_of(ctx, user),
    }


def _facts_of(ctx: Ctx, user: uuid.UUID) -> int:
    with ctx.factory() as s:
        q = (
            select(orm.ExtractedFactModel.id)
            .join(orm.CheckSessionModel, orm.CheckSessionModel.id == orm.ExtractedFactModel.check_session_id)
            .where(orm.CheckSessionModel.user_id == user)
        )
        return len(s.scalars(q).all())


def _set_role(ctx: Ctx, user: uuid.UUID, role: enums.UserRole) -> None:
    with ctx.factory() as s:
        s.execute(update(orm.UserModel).where(orm.UserModel.id == user).values(role=role))
        s.commit()


def _new_user(ctx: Ctx, role: enums.UserRole = enums.UserRole.CITIZEN) -> uuid.UUID:
    uid = uuid.uuid4()
    with ctx.factory() as s:
        s.add(orm.UserModel(id=uid, email=f"{uid.hex[:8]}@example.com", password_hash="x", role=role))
        s.commit()
    return uid


# ===========================================================================
# 1. 회원 탈퇴 + 이메일 가입·로그인
# ===========================================================================
def test_signup_and_login_with_email_password(ctx: Ctx) -> None:
    assert _signup(ctx, "New.User@Example.com") == 201
    assert _signup(ctx, "new.user@example.com") == 409  # 대소문자만 다른 같은 이메일
    code, uid, h = _login(ctx, "NEW.USER@example.com")  # 이메일은 소문자로 정규화
    assert code == 200 and uid is not None
    with ctx.factory() as s:
        row = s.get(orm.UserModel, uuid.UUID(uid))
        assert row is not None and row.email == "new.user@example.com"
        assert row.password_hash is not None and row.password_hash.startswith("$argon2id$")  # 평문 저장 안 함
        assert PASSWORD not in row.password_hash
        assert row.role == enums.UserRole.CITIZEN
    agree_all(ctx, uuid.UUID(uid))
    r = ctx.client.post("/api/v1/cases", headers=h, json={"title": "내 사건", "case_type": "OTHER_CONSUMER"})
    assert r.status_code == 201


@pytest.mark.parametrize(
    "password",
    # 7자(4종) / 1종 16자 / 2종 9자 / 3종 8자(통과 대조군) / 65자
    ["Shrt1!A", "alllowercaseonly", "abcdefg12", "Abcdefg1", "Ab1-" + "x" * 61],
)
def test_signup_rejects_weak_password(ctx: Ctx, password: str) -> None:
    ok = {"Abcdefg1": 201}  # 3종 8자 → 통과(대조군)
    assert _signup(ctx, "weak@example.com", password) == ok.get(password, 422)


def test_signup_rejects_password_containing_email_id_and_bad_email(ctx: Ctx) -> None:
    assert _signup(ctx, "hongildong@example.com", "Hongildong-2026") == 422
    assert _signup(ctx, "not-an-email", PASSWORD) == 422


def test_login_failure_is_uniform_and_locks_after_5_tries(ctx: Ctx) -> None:
    assert _signup(ctx, "lock@example.com") == 201
    assert _login(ctx, "nobody@example.com")[0] == 401  # 없는 이메일
    for _ in range(4):
        assert _login(ctx, "lock@example.com", "Wrong-pass-2026")[0] == 401  # 틀린 비밀번호(같은 응답)
    assert _login(ctx, "lock@example.com", "Wrong-pass-2026")[0] == 401  # 5회째 → 잠김
    assert _login(ctx, "lock@example.com")[0] == 429  # 맞는 비밀번호도 잠금 중 거부
    ctx.clock.current = datetime.now(UTC) + timedelta(minutes=11)
    r = ctx.client.post("/api/v1/auth/login", json={"email": "lock@example.com", "password": PASSWORD})
    assert r.status_code == 200  # 10분 뒤 해제
    with ctx.factory() as s:
        row = s.scalars(select(orm.UserModel).where(orm.UserModel.email == "lock@example.com")).one()
        assert row.failed_login_count == 0 and row.locked_until is None


def test_password_with_spaces_is_not_trimmed(ctx: Ctx) -> None:
    pw = " Spaced-pass-2026 "
    assert _signup(ctx, "blank@example.com", pw) == 201
    assert _login(ctx, "blank@example.com", pw.strip())[0] == 401
    assert _login(ctx, "blank@example.com", pw)[0] == 200


def test_withdraw_requires_correct_password(ctx: Ctx) -> None:
    h = ctx.auth(ctx.user_a)
    r = ctx.client.post("/api/v1/me/withdrawal", headers=h, json={"password": "Wrong-pass-2026"})
    assert r.status_code == 403 and r.json()["code"] == "REAUTH_FAILED"
    row = _user_row(ctx, ctx.user_a)
    assert row is not None and row.status == enums.UserStatus.ACTIVE  # 아무것도 바뀌지 않음
    assert row.failed_login_count == 1  # 재확인 실패도 실패 횟수에 포함
    assert ctx.client.get(f"/api/v1/cases/{uuid.uuid4()}", headers=h).status_code == 404  # 로그인은 유지


def test_withdraw_blocks_access_and_purges_all_user_data(ctx: Ctx) -> None:
    a, b = ctx.user_a, ctx.user_b
    # A: 사건으로 전환한 점검(보고서·사실·문서·스레드) + 미전환 점검(문서) + 직접 만든 사건
    sid, _, _ = _approved(ctx, a)
    case_id = _convert(ctx, a, sid)
    th = _thread(ctx, a, sid)
    sid2 = _session(ctx, a)
    _upload(ctx, a, sid2)
    ctx.client.post(
        "/api/v1/cases", headers=ctx.auth(a), json={"title": "직접", "case_type": "OTHER_CONSUMER"}
    )
    # B: 영향받으면 안 되는 자료
    sid_b, _, _ = _approved(ctx, b)
    before_b = _owned_rows(ctx, b)
    files_b = len(list(ctx.files_dir.iterdir())) - 2  # A 의 파일 2개를 뺀 수
    assert files_b == 1

    h = ctx.auth(a)
    assert _withdraw(ctx, h) == 204

    # (1) 접근 차단: 탈퇴 전에 받은 토큰·서비스 토큰 모두 401
    assert ctx.client.get(f"/api/v1/cases/{case_id}", headers=h).status_code == 401
    r = ctx.client.post("/api/v1/check-sessions", headers=h, json={"entry_type": "PRE_CONTRACT"})
    assert r.status_code == 401
    assert _withdraw(ctx, h) == 401
    r = ctx.client.post(f"/api/v1/check-sessions/{sid2}/facts", headers=ctx.svc(a), json={"facts": []})
    assert r.status_code == 401

    # (2) 즉시 파기: 점검·사건·문서·사실·동의 0건, 파일·체크포인트 삭제
    assert _owned_rows(ctx, a) == {"sessions": 0, "cases": 0, "documents": 0, "consents": 0, "facts": 0}
    assert len(list(ctx.files_dir.iterdir())) == files_b
    assert th in ctx.checkpoints.deleted

    # (3) 계정 행은 감사 로그 연결용 '빈 행'만 남는다(식별정보 없음)
    row = _user_row(ctx, a)
    assert row is not None and row.status == enums.UserStatus.WITHDRAWN
    assert row.email is None and row.password_hash is None and row.display_name is None
    assert row.deleted_at is not None

    # (4) 감사 로그에 탈퇴·파기 기록, 원문·이메일 없음
    with ctx.factory() as s:
        logs = s.scalars(select(orm.AuditLogModel).where(orm.AuditLogModel.target_id == a)).all()
        actions = {log.action for log in logs}
        assert {enums.AuditAction.DELETE, enums.AuditAction.PURGE} <= actions
        assert all("example.com" not in str(log.detail) for log in logs)

    # (5) 다른 사용자 자료는 그대로
    assert _owned_rows(ctx, b) == before_b
    assert ctx.client.get(f"/api/v1/cases/{_convert(ctx, b, sid_b)}", headers=ctx.auth(b)).status_code == 200


def test_withdraw_then_same_email_signs_up_as_new_member(ctx: Ctx) -> None:
    assert _signup(ctx, "again@example.com") == 201
    _, uid, h = _login(ctx, "again@example.com")
    assert _withdraw(ctx, h) == 204
    assert _login(ctx, "again@example.com")[0] == 401  # 탈퇴 계정으로는 로그인 불가
    assert _signup(ctx, "again@example.com") == 201  # 같은 이메일로 새로 가입 가능
    _, uid2, _ = _login(ctx, "again@example.com")
    assert uid2 != uid  # 탈퇴 전 자료와 연결되지 않는다


def test_purge_failure_after_withdrawal_is_retried_by_batch(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = ctx.user_a
    _approved(ctx, a)

    def boom() -> object:
        raise RuntimeError("storage down")

    monkeypatch.setattr(ctx.container, "purge_user_data", boom)
    assert _withdraw(ctx, ctx.auth(a)) == 204  # 탈퇴 자체는 확정
    assert _count(ctx, orm.CheckSessionModel, user_id=a) == 1  # 아직 남음(논리 삭제 상태)
    with ctx.factory() as s:
        assert s.scalars(select(orm.CheckSessionModel.deleted_at)).one() is not None
    monkeypatch.undo()
    report = ctx.container.purge_expired_data().execute()  # 정기 배치(기간 없이 즉시 대상)
    assert report.withdrawn_users == 1 and report.sessions == 1
    assert _owned_rows(ctx, a) == {"sessions": 0, "cases": 0, "documents": 0, "consents": 0, "facts": 0}


def test_withdrawn_account_row_and_audit_logs_expire_after_90_days(ctx: Ctx) -> None:
    a = ctx.user_a
    agree_all(ctx, a)
    assert _withdraw(ctx, ctx.auth(a)) == 204
    ctx.clock.current = NOW + timedelta(days=89)
    assert ctx.container.purge_expired_data().execute().accounts == 0
    assert _user_row(ctx, a) is not None
    ctx.clock.current = NOW + timedelta(days=91)
    report = ctx.container.purge_expired_data().execute()
    assert report.audit_logs > 0 and report.accounts == 1
    assert _user_row(ctx, a) is None
    with ctx.factory() as s:  # 남은 감사 로그는 배치 자신의 기록(90일 이내)뿐
        rows = s.scalars(select(orm.AuditLogModel)).all()
        assert all(r.created_at.replace(tzinfo=UTC) >= NOW + timedelta(days=1) for r in rows)


# ===========================================================================
# 2. 미전환 점검 파기 · 종결 사건 · 감사 로그 보관 기간
# ===========================================================================
def test_unconverted_session_is_purged_30_days_after_last_use(ctx: Ctx) -> None:
    a, b = ctx.user_a, ctx.user_b
    idle, _, _ = _approved(ctx, a)  # 미전환(보고서까지 승인했지만 사건 전환 안 함)
    th = _thread(ctx, a, idle)
    converted, _, _ = _approved(ctx, a)
    _convert(ctx, a, converted)  # 전환된 점검은 대상 아님
    other = _session(ctx, b)  # B 의 점검: 나중에 다시 사용

    ctx.clock.current = NOW + timedelta(days=20)
    _upload(ctx, b, other)  # B 는 20일째에 문서를 올려 '마지막 이용'이 늦춰짐

    ctx.clock.current = NOW + timedelta(days=29)
    report = ctx.container.purge_expired_data().execute()
    assert report.sessions == 0
    assert _count(ctx, orm.CheckSessionModel) == 3

    ctx.clock.current = NOW + timedelta(days=31)
    report = ctx.container.purge_expired_data().execute()
    assert report.sessions == 1 and report.cases == 0
    with ctx.factory() as s:
        remaining = set(s.scalars(select(orm.CheckSessionModel.id)).all())
    assert remaining == {uuid.UUID(converted), uuid.UUID(other)}
    assert _count(ctx, orm.DocumentModel, check_session_id=uuid.UUID(idle)) == 0
    assert _count(ctx, orm.ExtractedFactModel, check_session_id=uuid.UUID(idle)) == 0
    assert _count(ctx, orm.InspectionReportModel, check_session_id=uuid.UUID(idle)) == 0
    assert th in ctx.checkpoints.deleted
    assert len(list(ctx.files_dir.iterdir())) == 2  # 전환된 점검 1개 + B 의 문서 1개

    ctx.clock.current = NOW + timedelta(days=51)  # B 의 마지막 이용(20일) + 31일
    assert ctx.container.purge_expired_data().execute().sessions == 1
    assert _count(ctx, orm.CheckSessionModel) == 1


def test_closed_case_is_purged_30_days_after_closing_active_case_is_kept(ctx: Ctx) -> None:
    a = ctx.user_a
    closed_sid, _, _ = _approved(ctx, a)
    closed = _convert(ctx, a, closed_sid)
    open_sid, _, _ = _approved(ctx, a)
    active = _convert(ctx, a, open_sid)
    with ctx.factory() as s:
        s.execute(
            update(orm.CaseModel)
            .where(orm.CaseModel.id == uuid.UUID(closed))
            .values(status=enums.CaseStatus.CLOSED, closed_at=NOW)
        )
        s.commit()
    ctx.clock.current = NOW + timedelta(days=29)
    assert ctx.container.purge_expired_data().execute().cases == 0
    ctx.clock.current = NOW + timedelta(days=200)
    report = ctx.container.purge_expired_data().execute()
    assert report.cases == 1  # 종결 사건만. 진행 중 사건은 기간과 무관하게 유지(별도 정책 전까지)
    with ctx.factory() as s:
        assert set(s.scalars(select(orm.CaseModel.id)).all()) == {uuid.UUID(active)}
        assert s.get(orm.CheckSessionModel, uuid.UUID(closed_sid)) is None  # 원본 점검도 함께


def test_retention_periods_come_from_settings(ctx: Ctx) -> None:
    from app.application.use_cases.agent import PurgeExpiredData, RetentionPolicy
    from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

    _session(ctx, ctx.user_a)
    ctx.clock.current = NOW + timedelta(days=8)
    short = PurgeExpiredData(
        SqlAlchemyUnitOfWork(ctx.factory),
        ctx.clock,
        ctx.container.storage,
        ctx.checkpoints,
        RetentionPolicy(unconverted_session_days=7),
    )
    assert short.execute().sessions == 1


# ===========================================================================
# 3. 사용자별 접근 제한(관리자·상담사도 시민 원문 접근 불가)
# ===========================================================================
@pytest.mark.parametrize("role", [enums.UserRole.ADMIN, enums.UserRole.COUNSELOR])
def test_admin_and_counselor_cannot_reach_citizen_records(ctx: Ctx, role: enums.UserRole) -> None:
    a = ctx.user_a
    sid, rid, _ = _approved(ctx, a)
    case_id = _convert(ctx, a, sid)
    with ctx.factory() as s:
        fid = s.scalars(
            select(orm.ExtractedFactModel.id).where(orm.ExtractedFactModel.check_session_id == uuid.UUID(sid))
        ).first()
    staff = _new_user(ctx, role)
    agree_all(ctx, staff)
    h = ctx.auth(staff)
    checks = [
        ctx.client.get(f"/api/v1/cases/{case_id}", headers=h),
        ctx.client.delete(f"/api/v1/cases/{case_id}", headers=h),
        ctx.client.post(
            f"/api/v1/cases/{case_id}/events",
            headers=h,
            json={"occurred_on": "2026-11-01", "actor": "USER", "title": "x"},
        ),
        ctx.client.post(f"/api/v1/cases/{case_id}/consultation-preps", headers=h, json={}),
        ctx.client.post(f"/api/v1/reports/{rid}/approval", headers=h),
        ctx.client.post(f"/api/v1/facts/{fid}/confirmation", headers=h, json={"action": "CONFIRM"}),
        ctx.client.post(
            f"/api/v1/check-sessions/{sid}/case-conversion", headers=h, json={"title": "가로채기"}
        ),
        ctx.client.post("/api/v1/agent-threads", headers=h, json={"check_session_id": sid}),
        ctx.client.post(
            f"/api/v1/check-sessions/{sid}/documents",
            headers=h,
            files={"file": ("a.pdf", b"%PDF-1.4\n", "application/pdf")},
        ),
    ]
    assert [r.status_code for r in checks] == [404] * len(checks)
    # 역할이 있어도 '본인 자료' 원칙은 같다: 자기 사건은 정상
    r = ctx.client.post("/api/v1/cases", headers=h, json={"title": "own", "case_type": "OTHER_CONSUMER"})
    assert r.status_code == 201


def test_dormant_account_tokens_are_rejected(ctx: Ctx) -> None:
    with ctx.factory() as s:
        s.execute(
            update(orm.UserModel)
            .where(orm.UserModel.id == ctx.user_b)
            .values(status=enums.UserStatus.DORMANT)
        )
        s.commit()
    r = ctx.client.post(
        "/api/v1/cases", headers=ctx.auth(ctx.user_b), json={"title": "x", "case_type": "OTHER_CONSUMER"}
    )
    assert r.status_code == 401
    # 존재하지 않는 사용자 ID 의 (서명은 맞는) 토큰도 401
    assert ctx.client.get(f"/api/v1/cases/{uuid.uuid4()}", headers=ctx.auth(uuid.uuid4())).status_code == 401


# ===========================================================================
# 4. AI 내부 API 권한 분리(R-01)
# ===========================================================================
def _fact_body() -> dict[str, object]:
    return {
        "facts": [{"fact_key": "BUSINESS_NAME", "value_text": "가나 필라테스", "source_kind": "STATEMENT"}]
    }


def _report_body() -> dict[str, object]:
    return {"summary_text": "요약", "items": [{"category": "UNVERIFIED", "content": "확인 필요"}]}


def test_citizen_token_cannot_call_ai_internal_apis(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    h = ctx.auth(ctx.user_a)
    r1 = ctx.client.post(f"/api/v1/check-sessions/{sid}/facts", headers=h, json=_fact_body())
    r2 = ctx.client.post(f"/api/v1/check-sessions/{sid}/reports", headers=h, json=_report_body())
    assert (r1.status_code, r2.status_code) == (403, 403)
    assert _count(ctx, orm.ExtractedFactModel) == 0
    # 토큰 없음·위조 토큰은 401
    assert ctx.client.post(f"/api/v1/check-sessions/{sid}/facts", json=_fact_body()).status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert (
        ctx.client.post(f"/api/v1/check-sessions/{sid}/facts", headers=bad, json=_fact_body()).status_code
        == 401
    )


def test_service_token_works_only_with_scope_and_owner(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    url = f"/api/v1/check-sessions/{sid}/facts"
    assert ctx.client.post(url, headers=ctx.svc(ctx.user_a), json=_fact_body()).status_code == 201
    # 권한 범위가 다르면 403
    only_reports = ctx.svc(ctx.user_a, (SCOPE_REPORTS_WRITE,))
    assert ctx.client.post(url, headers=only_reports, json=_fact_body()).status_code == 403
    # 다른 시민 대상(obo=B)으로 A 의 점검에 쓰기 → 404(존재 은닉)
    assert ctx.client.post(url, headers=ctx.svc(ctx.user_b), json=_fact_body()).status_code == 404
    assert ctx.svc(ctx.user_a, (SCOPE_FACTS_WRITE,))  # 사용 예


def test_service_token_cannot_be_used_on_citizen_apis_or_forged(ctx: Ctx) -> None:
    sid = _session(ctx, ctx.user_a)
    svc = ctx.svc(ctx.user_a)
    assert ctx.client.get(f"/api/v1/cases/{uuid.uuid4()}", headers=svc).status_code == 401
    assert (
        ctx.client.post(
            "/api/v1/check-sessions", headers=svc, json={"entry_type": "PRE_CONTRACT"}
        ).status_code
        == 401
    )
    assert _withdraw(ctx, svc) == 401
    url = f"/api/v1/check-sessions/{sid}/facts"
    now = datetime.now(UTC)
    claims = {
        "sub": "ai-agent",
        "iss": "gwonri-chaebi",
        "aud": "gwonri-ai-internal",
        "obo": str(ctx.user_a),
        "scope": f"{SCOPE_FACTS_WRITE} {SCOPE_REPORTS_WRITE}",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    # 시민 비밀키로 서명한 '서비스 토큰' 위조 → 401
    forged = jwt.encode(claims, SECRET, algorithm="HS256")
    assert (
        ctx.client.post(url, headers={"Authorization": f"Bearer {forged}"}, json=_fact_body()).status_code
        == 401
    )
    # 유효기간 15분 초과 → 401
    long = jwt.encode({**claims, "exp": now + timedelta(hours=2)}, SERVICE_SECRET, algorithm="HS256")
    assert (
        ctx.client.post(url, headers={"Authorization": f"Bearer {long}"}, json=_fact_body()).status_code
        == 401
    )
    # aud 없음 → 401
    no_aud = jwt.encode({k: v for k, v in claims.items() if k != "aud"}, SERVICE_SECRET, algorithm="HS256")
    assert (
        ctx.client.post(url, headers={"Authorization": f"Bearer {no_aud}"}, json=_fact_body()).status_code
        == 401
    )


def test_ai_internal_api_is_closed_when_service_secret_is_not_set(
    ctx: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.infrastructure.services import ServiceTokenVerifier

    sid = _session(ctx, ctx.user_a)
    svc = ctx.svc(ctx.user_a)  # 설정을 끄기 전에 발급
    monkeypatch.setattr(ctx.container, "service_verifier", ServiceTokenVerifier(None))
    url = f"/api/v1/check-sessions/{sid}/facts"
    assert ctx.client.post(url, headers=svc, json=_fact_body()).status_code == 401
    assert ctx.client.post(url, headers=ctx.auth(ctx.user_a), json=_fact_body()).status_code == 403


def test_settings_reject_same_secret_for_citizen_and_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    monkeypatch.setenv("HJ_JWT_SECRET", SECRET)
    monkeypatch.setenv("HJ_SERVICE_JWT_SECRET", SECRET)
    with pytest.raises(RuntimeError):
        Settings.from_env()


# ===========================================================================
# 벡터 지식 DB(A안: pgvector, knowledge 스키마) — PostgreSQL 에서만
# ===========================================================================
def test_pgvector_knowledge_base_loads_verifies_and_searches(ctx: Ctx) -> None:
    import os
    from datetime import date
    from pathlib import Path

    from app.infrastructure.db.engine import build_session_factory
    from app.infrastructure.knowledge import KnowledgeChunk, KnowledgeSource
    from app.infrastructure.pgvector_knowledge import PgVectorKnowledgeBase, load_knowledge

    url = os.environ.get("HJ_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("pgvector 시험은 PostgreSQL 에서만")
    engine, _ = build_session_factory(url)
    ddl = (Path(__file__).resolve().parents[1] / "db" / "knowledge_schema.sql").read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
            cur.execute(ddl)
        raw.commit()
    finally:
        raw.close()
    src = KnowledgeSource(
        "law", "LAW", "시험 법령", "법제처", "https://www.law.go.kr/", "t", True, date(2025, 1, 1)
    )
    old = KnowledgeSource("old", "NOTICE", "폐지 고시", "공정위", "https://example.test/", "t", False)
    chunks = [
        KnowledgeChunk("law#0", "law", 0, "환급 기준 본문", "제1조"),
        KnowledgeChunk("law#1", "law", 1, "위약금 본문", "제2조"),
        KnowledgeChunk("old#0", "old", 0, "폐지된 기준", None),
    ]
    emb = {"law#0": [1.0, 0.0, 0.0], "law#1": [0.0, 1.0, 0.0], "old#0": [0.99, 0.1, 0.0]}
    assert load_knowledge(engine, [src, old], chunks, emb, "test-3d") == 3
    assert load_knowledge(engine, [src, old], chunks, emb, "test-3d") == 3  # 재적재(멱등)
    kb = PgVectorKnowledgeBase(engine)
    facts = kb.chunk_facts(["law#0", "old#0", "none"])
    assert set(facts) == {"law#0", "old#0"}
    assert facts["law#0"].article_ref == "제1조" and facts["old#0"].source_active is False
    hits = kb.similar_chunks([1.0, 0.05, 0.0], k=2)
    assert [h.chunk_id for h in hits] == ["law#0", "law#1"]  # 폐지 출처 제외
    assert kb.similar_chunks([1.0, 0.05, 0.0], k=1, active_only=False)[0].chunk_id in {"law#0", "old#0"}
    # 업무 DB 와 FK 없음: knowledge 스키마의 외래키는 knowledge 안에서만
    with engine.connect() as conn:
        refs = (
            conn.exec_driver_sql(
                "SELECT DISTINCT ccu.table_schema FROM information_schema.table_constraints tc "
                "JOIN information_schema.constraint_column_usage ccu "
                "ON tc.constraint_name = ccu.constraint_name "
                "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'knowledge'"
            )
            .scalars()
            .all()
        )
    assert refs == ["knowledge"]
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA knowledge CASCADE")
    engine.dispose()
