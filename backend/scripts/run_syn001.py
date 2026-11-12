"""첫 합성 사례를 실제 localhost HTTP로 연결한다(모델·사용자 동작은 대역).

검증: python -m scripts.run_syn001 --verify --output var/syn001_verify
화면 인계: python -m scripts.run_syn001 --serve --port 8000 --output var/syn001_serve

운영 설정을 읽지 않으며 HJ_* 환경변수가 있으면 시작하지 않는다. 새 SQLite만 사용한다.
--serve의 시험 로그인 정보는 var/ 아래 private_credentials.json(0600)에만 저장한다.
프론트 수정, 실제 모델 호출, 승인 자동화, 외부 서비스 읽기 scope 추가는 하지 않는다.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from app.application.use_cases.account import SignUpCommand
from app.container import Container
from app.domain.enums import FactStatus, SessionStatus
from app.domain.errors import NotFoundError
from app.infrastructure.db.base import Base
from app.infrastructure.db.engine import build_session_factory
from app.infrastructure.knowledge import JsonAgencyDirectory, LocalKnowledgeBase
from app.infrastructure.services import (
    Argon2PasswordHasher,
    JwtTokenVerifier,
    LocalFileStorage,
    ServiceTokenVerifier,
    SystemClock,
)
from app.interface.api.dependencies import SCOPE_FACTS_WRITE, SCOPE_REPORTS_WRITE
from app.main import create_app

API = "/api/v1"
ROOT = Path(__file__).resolve().parents[1]
ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
LABELS = {
    "BUSINESS_NAME": "사업자명",
    "CONTRACT_PERIOD": "계약기간",
    "PAYMENT_AMOUNT": "결제금액",
    "USED_SESSIONS": "실제 이용횟수",
    "CONTRACT_DATE": "계약일",
    "CANCEL_REQUEST_DATE": "해지 요청일",
    "OTHER": "계약서 사본 보유 여부",
}


class RunnerError(RuntimeError):
    """비밀이 들어가지 않는 실행 단계 오류."""


class StaleDraftError(RunnerError):
    """화면 수정과 초안 제출이 겹쳐 현재 사실을 다시 읽어야 한다."""


def require(condition: bool, label: str) -> None:
    if not condition:
        raise RunnerError(label)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(word in key.lower() for word in ("password", "token", "secret", "authorization"))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(redact(value), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


@dataclasses.dataclass(frozen=True)
class SessionContext:
    """인증된 합성 사용자 동작에서 만든 동일 프로세스 문맥. 외부 요청 DTO가 아니다."""

    user_id: uuid.UUID
    session_id: uuid.UUID


class LocalRun:
    def __init__(self, db_root: Path, output: Path, port: int) -> None:
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.trace = (output / "redacted_trace.jsonl").open("w", encoding="utf-8")
        self.http_count = 0
        self.last_step = "격리 환경 생성"
        self.checks: list[dict[str, Any]] = []
        self.engine, self.factory = build_session_factory(f"sqlite:///{db_root / 'synthetic.sqlite3'}")
        Base.metadata.create_all(self.engine)
        self.container = Container(
            self.factory,
            SystemClock(),
            LocalFileStorage(db_root / "files"),
            JwtTokenVerifier(secrets.token_urlsafe(48)),
            service_verifier=ServiceTokenVerifier(secrets.token_urlsafe(48)),
            password_hasher=Argon2PasswordHasher(time_cost=1, memory_cost=8 * 1024),
            knowledge=LocalKnowledgeBase([], []),
            agencies=JsonAgencyDirectory([]),
            public_signup_enabled=False,
        )
        self.accounts = []
        for index in (1, 2):
            email = f"synthetic-{index}@example.invalid"
            password = "Syn!Aa9-" + secrets.token_urlsafe(20)
            user_id = self.container.sign_up().execute(SignUpCommand(email, password, "합성 시험계정"))
            self.accounts.append({"email": email, "password": password, "user_id": user_id})
        self.user_id = self.accounts[0]["user_id"]
        self.other_user_id = self.accounts[1]["user_id"]
        self._contexts: set[SessionContext] = set()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(128)
        self.base_url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_app(self.container, cors_origins=ORIGINS),
                host="127.0.0.1",
                port=self.sock.getsockname()[1],
                log_level="error",
                access_log=False,
            )
        )
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        require(self.server.started, "localhost 서버 기동 실패")
        self.client = httpx.Client(base_url=self.base_url, timeout=10, trust_env=False)
        self.user_headers: list[dict[str, str]] = []
        for account in self.accounts:
            login = self.request(
                "시험계정 로그인",
                "POST",
                f"{API}/auth/login",
                200,
                body={"email": account["email"], "password": account["password"]},
            )
            claims = self.container.token_verifier.claims(login["access_token"])
            require(claims.user_id == account["user_id"], "로그인 사용자 문맥 불일치")
            self.user_headers.append({"Authorization": f"Bearer {login['access_token']}"})
        self.headers = self.user_headers[0]
        for consent in ("PRIVACY_REQUIRED", "SENSITIVE_DOCUMENT"):
            self.request(
                "합성 이용 동의",
                "POST",
                f"{API}/consents",
                201,
                headers=self.headers,
                body={"consent_type": consent, "policy_version": "synthetic-20261112", "agreed": True},
            )

    def close(self) -> None:
        if hasattr(self, "client"):
            self.client.close()
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()
        self.engine.dispose()
        self.trace.close()

    def service_headers(self, scope: str) -> dict[str, str]:
        # 기존 쓰기 권한 하나만 발급한다. 읽기 권한은 만들거나 확장하지 않는다.
        token = self.container.service_verifier.issue("synthetic-in-process", self.user_id, [scope])
        return {"Authorization": f"Bearer {token}"}

    def request(
        self,
        label: str,
        method: str,
        path: str,
        expected: int | tuple[int, ...],
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> Any:
        self.last_step = label
        response = self.client.request(method, path, json=body, headers=headers, files=files)
        self.http_count += 1
        payload = response.json() if response.content else None
        record = {
            "sequence": self.http_count,
            "at": datetime.now(UTC).isoformat(),
            "step": label,
            "method": method,
            "path": path,
            "request_body": body,
            "status_code": response.status_code,
            "expected_status": expected,
            "response": payload,
        }
        if files:
            record["request_files"] = {
                key: {"filename": value[0], "bytes": len(value[1]), "mime_type": value[2]}
                for key, value in files.items()
            }
        self.trace.write(json.dumps(redact(record), ensure_ascii=False, default=str) + "\n")
        self.trace.flush()
        allowed = (expected,) if isinstance(expected, int) else expected
        require(
            response.status_code in allowed, f"{label}: 예상 HTTP {expected}, 실제 {response.status_code}"
        )
        return payload

    def seed(
        self, label: str = "SYN-001", *, all_unknown: bool = False
    ) -> tuple[SessionContext, dict[str, str]]:
        session = self.request(
            f"{label} 상황 입력",
            "POST",
            f"{API}/check-sessions",
            201,
            headers=self.headers,
            body={
                "entry_type": "REFUND_CONCERN",
                "situation_text": (
                    "합성 필라테스 6개월 회원권에 60만원을 결제하고 4회 이용했습니다. "
                    "계약일과 해지 요청일, 계약서 사본 보유 여부는 아직 모릅니다."
                    if not all_unknown
                    else "합성 경계시험: 계약일과 해지 요청일 등 모든 사실을 모릅니다."
                ),
            },
        )
        context = SessionContext(self.user_id, uuid.UUID(session["id"]))
        # 로그인으로 확인한 사용자와 그 사용자가 HTTP로 생성한 점검만 내부 작업 문맥에 등록.
        self._contexts.add(context)
        facts = (
            []
            if all_unknown
            else [
                {"fact_key": key, "value_text": value, "source_kind": "STATEMENT"}
                for key, value in (
                    ("BUSINESS_NAME", "합성 필라테스"),
                    ("CONTRACT_PERIOD", "6개월"),
                    ("PAYMENT_AMOUNT", "600,000원"),
                    ("USED_SESSIONS", "6회"),
                )
            ]
        )
        facts += [
            {"fact_key": key, "source_kind": "UNKNOWN"}
            for key in ("CONTRACT_DATE", "CANCEL_REQUEST_DATE", "OTHER")
        ]
        result = self.request(
            f"{label} 추출 대역 등록",
            "POST",
            f"{API}/check-sessions/{context.session_id}/facts",
            201,
            headers=self.service_headers(SCOPE_FACTS_WRITE),
            body={"facts": facts},
        )
        return context, {fact["fact_key"]: fid for fact, fid in zip(facts, result["ids"], strict=True)}

    def read_for_ai(self, context: SessionContext) -> Any:
        # 시민 토큰을 AI에 넘기지 않는다. 다른 서비스의 읽기 API 구현을 뜻하지 않는다.
        require(context in self._contexts, "등록되지 않은 동일 프로세스 점검 문맥")
        bound = self.container.for_user(context.user_id)
        bound.ensure_active_user().execute(context.user_id)
        return bound.list_session_facts().execute(context.user_id, context.session_id)

    def confirm(self, ids: dict[str, str], *, correct_used: bool = True) -> None:
        for key, fid in ids.items():
            if key in ("CONTRACT_DATE", "CANCEL_REQUEST_DATE", "OTHER"):
                body: dict[str, Any] = {"action": "ACKNOWLEDGE_UNKNOWN"}
            elif key == "USED_SESSIONS" and correct_used:
                body = {"action": "MODIFY", "new_value": "4회"}
            else:
                body = {"action": "CONFIRM"}
            self.request(
                f"{LABELS[key]} 사용자 동작 대역",
                "POST",
                f"{API}/facts/{fid}/confirmation",
                200,
                headers=self.headers,
                body=body,
            )

    def report_body(self, context: SessionContext) -> dict[str, Any]:
        detail = self.read_for_ai(context)
        items = []
        for fact in detail.facts:
            if fact.is_confirmed:
                items.append(
                    {
                        "category": "CONFIRMED_FACT",
                        "fact_id": str(fact.id),
                        "content": f"{LABELS[str(fact.fact_key)]}: {fact.value_text} (사용자 진술)",
                    }
                )
            elif fact.status == FactStatus.ACKNOWLEDGED_UNKNOWN:
                items.append(
                    {
                        "category": "UNVERIFIED",
                        "fact_id": str(fact.id),
                        "content": f"{LABELS[str(fact.fact_key)]} 미확인",
                    }
                )
        # 팀 인용 승인 증거가 없으므로 어떠한 청크도 인용하지 않는다.
        # CAUTION 무인용은 기존 서버 계약에서 NO_CITATION으로 보류된다.
        items.append(
            {
                "category": "CAUTION",
                "content": "공식 근거의 원문 대조와 팀 인용 승인이 확인되지 않아 해석을 보류합니다.",
                "citations": [],
            }
        )
        return {
            "summary_text": "사용자가 확인한 사실과 추가 확인이 필요한 항목을 정리했습니다.",
            "facts_confirmed_at": (
                detail.session.facts_confirmed_at.isoformat() if detail.session.facts_confirmed_at else None
            ),
            "items": items,
        }

    def submit(self, context: SessionContext, *, retry_stale: bool = False) -> str:
        result = self.request(
            "인용 미승인 보류 초안 생성",
            "POST",
            f"{API}/check-sessions/{context.session_id}/reports",
            (201, 409) if retry_stale else 201,
            headers=self.service_headers(SCOPE_REPORTS_WRITE),
            body=self.report_body(context),
        )
        if retry_stale and result.get("code") == "CONFLICT":
            raise StaleDraftError("사용자 수정 후 현재 사실 재확인 필요")
        require(result["held_items"] == 1, "미승인 근거 보류 수 불일치")
        return str(result["id"])

    def check(self, label: str, evidence: Any) -> None:
        self.checks.append({"check": label, "status": "PASS", "evidence": evidence})

    def connection(self, context: SessionContext, ids: dict[str, str], **extra: Any) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_prefix": API,
            "cors_origins": ORIGINS,
            "session_id": str(context.session_id),
            "fact_ids": ids,
            "scenario": "SYN-001: 필라테스 6개월·60만원·진술4회/추출대역6회",
            "pdf_submitted": False,
            "contract_copy_possession": "UNKNOWN",
            "extraction": "합성 대역",
            "actual_model": "미연결",
            "langgraph": "미연결",
            "ai_read": "동일 프로세스 사용자/점검 문맥에서 기존 유스케이스 재사용",
            "separate_service_read_api": "미구현",
            "team_citation_approval": "없음/보류",
            **extra,
        }

    def verify(self) -> dict[str, Any]:
        self.request("서버 상태 확인", "GET", "/health", 200)
        # 별도 기존 경로 보존 검증. SYN-001 점검에 PDF를 업로드하는 작업이 아니다.
        pdf = (ROOT / "tests" / "fixtures" / "synthetic_text_contract.pdf").read_bytes()
        extraction = self.request(
            "별도 합성 PDF의 기존 추출 경로 보존",
            "POST",
            "/api/contracts/extract-text",
            200,
            files={"file": ("synthetic_legacy_contract.pdf", pdf, "application/pdf")},
        )
        require(extraction["page_count"] > 0 and extraction["has_text"] is True, "기존 PDF 추출 실패")
        write_json(self.output / "legacy_pdf_extract.json", extraction)
        openapi = self.request("통합 앱 API 계약 확인", "GET", "/openapi.json", 200)
        # 계약의 password/token은 비밀값이 아닌 필드 정의다. 로그 마스킹으로 스키마를 훼손하지 않는다.
        (self.output / "openapi.json").write_text(
            json.dumps(openapi, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.check("통합 앱의 기존 health·PDF 추출 경로 보존", "실제 localhost HTTP200 / 별도 합성 PDF")
        context, ids = self.seed()
        self.request(
            "서비스 쓰기 토큰의 시민 조회 차단",
            "GET",
            f"{API}/check-sessions/{context.session_id}/facts",
            401,
            headers=self.service_headers(SCOPE_REPORTS_WRITE),
        )
        self.request(
            "다른 시민의 점검 조회 차단",
            "GET",
            f"{API}/check-sessions/{context.session_id}/facts",
            404,
            headers=self.user_headers[1],
        )
        try:
            other = self.container.for_user(self.other_user_id)
            other.ensure_active_user().execute(self.other_user_id)
            other.list_session_facts().execute(self.other_user_id, context.session_id)
        except NotFoundError:
            self.check("동일 프로세스 AI 읽기의 소유권 보호", "타사용자 유스케이스 조회 NotFoundError")
        else:
            raise RunnerError("동일 프로세스 조회의 소유권 보호 실패")
        self.confirm(ids)
        body = self.report_body(context)
        missing = dict(body, items=[i for i in body["items"] if i.get("fact_id") != ids["OTHER"]])
        self.request(
            "모름 항목 누락 보고서 차단",
            "POST",
            f"{API}/check-sessions/{context.session_id}/reports",
            422,
            headers=self.service_headers(SCOPE_REPORTS_WRITE),
            body=missing,
        )
        promoted = dict(
            body,
            items=[
                dict(item, category="CONFIRMED_FACT") if item.get("fact_id") == ids["OTHER"] else item
                for item in body["items"]
            ],
        )
        self.request(
            "모름의 확정 사실 승격 차단",
            "POST",
            f"{API}/check-sessions/{context.session_id}/reports",
            422,
            headers=self.service_headers(SCOPE_REPORTS_WRITE),
            body=promoted,
        )
        rid = self.submit(context)
        facts = self.request(
            "시민 화면용 사실 확인",
            "GET",
            f"{API}/check-sessions/{context.session_id}/facts",
            200,
            headers=self.headers,
        )
        values = {f["fact_key"]: f for f in facts["facts"]}
        require(values["USED_SESSIONS"]["ai_value"] == "6회", "초기 대역6회 보존 실패")
        require(values["USED_SESSIONS"]["current_value"] == "4회", "사용자 수정4회 반영 실패")
        unknown = [f for f in facts["facts"] if f["status"] == "ACKNOWLEDGED_UNKNOWN"]
        require(len(unknown) == 3 and all(f["current_value"] is None for f in unknown), "모름3건 유지 실패")
        draft = self.request("보류 초안 조회", "GET", f"{API}/reports/{rid}", 200, headers=self.headers)
        require(draft["held_items"] == 1, "초안 보류 항목 불일치")
        require(any(i["hold_reason"] == "NO_CITATION" for i in draft["items"]), "NO_CITATION 미보존")
        require(not any(i["citations"] for i in draft["items"]), "미승인 인용이 포함됨")
        write_json(self.output / "facts.json", facts)
        write_json(self.output / "report_draft.json", draft)
        self.check(
            "첫 실제 HTTP 연결: 6→4·모름3·근거보류1",
            {"session_id": str(context.session_id), "report_id": rid},
        )
        self.request(
            "사용자 보고서 승인 대역", "POST", f"{API}/reports/{rid}/approval", 204, headers=self.headers
        )
        converted = self.request(
            "승인 보고서 사건 전환",
            "POST",
            f"{API}/check-sessions/{context.session_id}/case-conversion",
            201,
            headers=self.headers,
            body={"title": "합성 SYN-001 날짜 미확인 상담 준비"},
        )
        cid = converted["id"]
        created_prep = self.request(
            "날짜0건 상담 초안 생성",
            "POST",
            f"{API}/cases/{cid}/consultation-preps",
            201,
            headers=self.headers,
            body={},
        )
        require(created_prep == {"case_id": cid, "version": 1}, "기존 상담 준비 POST 응답 계약 불일치")
        case = self.request("사건 상세 확인", "GET", f"{API}/cases/{cid}", 200, headers=self.headers)
        require(case["events"] == [] and case["evidences"] == [], "가짜 이벤트/문서 생성됨")
        require(len(case["snapshot"]["facts"]) == 4, "사건 사실 Snapshot 수 불일치")
        consultation = self.request(
            "상담 화면용 저장자료 조회",
            "GET",
            f"{API}/cases/{cid}/consultation-preps",
            200,
            headers=self.headers,
        )
        require(
            consultation["case_id"] == cid
            and consultation["version"] == created_prep["version"]
            and consultation["status"] == "DRAFT"
            and consultation["approved_at"] is None,
            "상담 조회 사건·버전·미승인 상태 불일치",
        )
        summary = consultation["summary_text"]
        require("날짜 미확인" in summary, "날짜 미확인 표시 누락")
        require("6회" not in summary and "사본 없음" not in summary, "대역 오류 또는 사본 없음 단정")
        for fact in case["snapshot"]["facts"]:
            require(f"{LABELS[fact['key']]}: {fact['value']}" in summary, "알려진 사실4건 본문 반영 실패")
        unknown_questions = {
            f"확인 필요: {LABELS[key]} 미확인" for key in ("CONTRACT_DATE", "CANCEL_REQUEST_DATE", "OTHER")
        }
        held_questions = {f"확인 필요: {item['content']}" for item in draft["items"] if item["hold_reason"]}
        require(len(unknown_questions) == 3 and len(held_questions) == 1, "합성 질문 구성 불일치")
        require(
            len(consultation["questions"]) == 4
            and set(consultation["questions"]) == unknown_questions | held_questions,
            "모름질문3건·근거보류질문1건 보존 실패",
        )
        require(consultation["checklist"] == [], "PDF 미제출 사례에 가짜 자료 생성됨")
        self.request(
            "다른 시민의 상담자료 조회 차단",
            "GET",
            f"{API}/cases/{cid}/consultation-preps",
            404,
            headers=self.user_headers[1],
        )
        write_json(
            self.output / "consultation.json",
            {
                "observation": "실제 localhost HTTP GET으로 읽은 저장된 상담 준비자료",
                "direct_database_observation": False,
                "unknown_questions_count": len(unknown_questions),
                "held_evidence_questions_count": len(held_questions),
                "data": consultation,
            },
        )
        write_json(self.output / "case.json", case)
        report = self.request(
            "승인 보고서 보존 확인", "GET", f"{API}/reports/{rid}", 200, headers=self.headers
        )
        write_json(self.output / "report.json", report)
        self.check("날짜0건 상담201/사건Snapshot4건/가짜날짜0건", {"case_id": cid, "version": 1})
        self.check(
            "상담자료GET200·타사용자404/모름질문3건·근거보류질문1건",
            {"case_id": cid, "version": consultation["version"], "data": "consultation.json의 실제 GET 응답"},
        )

        # 오래된 초안은 별도 초기 상태에서 시험해 첫 SYN-001의 결과를 덮어쓰지 않는다.
        stale_context, stale_ids = self.seed("별도 오래된 초안 경계시험")
        self.confirm(stale_ids, correct_used=False)
        stale_rid = self.submit(stale_context)
        self.request(
            "별도 초안 생성 후 사실 수정",
            "POST",
            f"{API}/facts/{stale_ids['USED_SESSIONS']}/confirmation",
            200,
            headers=self.headers,
            body={"action": "MODIFY", "new_value": "4회"},
        )
        self.request(
            "오래된 초안 승인 차단", "POST", f"{API}/reports/{stale_rid}/approval", 409, headers=self.headers
        )
        stale = self.request(
            "오래된 초안 상태 확인", "GET", f"{API}/reports/{stale_rid}", 200, headers=self.headers
        )
        require(stale["is_stale"] is True, "오래된 초안 표시 실패")
        fresh_rid = self.submit(stale_context)
        self.request(
            "새 초안 사용자 승인 대역",
            "POST",
            f"{API}/reports/{fresh_rid}/approval",
            204,
            headers=self.headers,
        )
        self.check(
            "별도 초기상태: 오래된초안409/새초안승인204", {"old_report": stale_rid, "new_report": fresh_rid}
        )

        unknown_context, unknown_ids = self.seed("별도 전부 모름 경계시험", all_unknown=True)
        self.confirm(unknown_ids)
        self.request(
            "전부 모름 보고서 차단",
            "POST",
            f"{API}/check-sessions/{unknown_context.session_id}/reports",
            409,
            headers=self.service_headers(SCOPE_REPORTS_WRITE),
            body=self.report_body(unknown_context),
        )
        self.check("모름 누락/승격422·전부모름409", "redacted_trace.jsonl의 실제 HTTP 응답")
        write_json(self.output / "connection.json", self.connection(context, ids, report_id=rid, case_id=cid))
        return {
            "status": "PASS",
            "mode": "verify",
            "http_requests": self.http_count,
            "checks": self.checks,
            "transport": "실제 localhost HTTP + FastAPI + 임시 SQLite",
            "extraction": "합성 대역",
            "user_actions": "합성 대역",
            "actual_model": "미연결",
            "frontend_qa": "NOT RUN",
            "postgresql_rls": "NOT RUN",
            "langgraph_checkpoint": "미연결",
            "full_purge": "NOT RUN",
            "team_citation_approval": "없음/NO_CITATION 보류",
            "ai_read": "등록된 동일 프로세스 사용자/점검 문맥 + 활성계정·소유권 검사",
            "separate_service_read_api": "미구현",
            "database_after_exit": "임시DB 삭제",
        }

    def serve(self, credentials_path: Path) -> None:
        context, ids = self.seed()
        payload = {
            "base_url": self.base_url,
            "login_path": f"{API}/auth/login",
            "accounts": [
                {"email": account["email"], "password": account["password"]} for account in self.accounts
            ],
        }
        fd = os.open(credentials_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        connection = self.connection(
            context,
            ids,
            credentials_file=str(credentials_path.relative_to(ROOT)),
            watcher="알려진 사실 확인·모름 인지 완료 후 보고서만 생성. 자동 승인/사건 전환 없음.",
        )
        write_json(self.output / "connection.json", connection)
        write_json(
            self.output / "result.json",
            {
                "status": "NOT RUN",
                "mode": "serve",
                "server": "RUNNING",
                "frontend_qa": "NOT RUN",
                "meaning": "초기 대역 사실6회·미처리 모름 상태. 실제 사용자의 화면 작업을 기다립니다.",
            },
        )
        print(
            json.dumps(
                {
                    "server": "RUNNING",
                    "base_url": self.base_url,
                    "connection": str(self.output / "connection.json"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        last_basis = None
        while self.thread.is_alive():
            detail = self.read_for_ai(context)
            basis = detail.session.facts_confirmed_at
            if detail.session.status == SessionStatus.FACTS_CONFIRMED and basis != last_basis:
                try:
                    rid = self.submit(context, retry_stale=True)
                except StaleDraftError:
                    # 읽기와 제출 사이의 사실 수정은 정상적인 409 경계다.
                    # 같은 사용자·점검을 다음 회차에 다시 읽고 새 초안을 제출한다.
                    time.sleep(0.1)
                    continue
                last_basis = basis
                connection.update(report_id=rid, facts_confirmed_at=basis.isoformat())
                write_json(self.output / "connection.json", connection)
                bound = self.container.for_user(context.user_id)
                bound.ensure_active_user().execute(context.user_id)
                stored_report = bound.get_report_detail().execute(context.user_id, uuid.UUID(rid))
                write_json(
                    self.output / "report.json",
                    {
                        "observation": "동일 프로세스 문맥에서 읽은 보고서. 시민 HTTP 토큰 대여 없음.",
                        "report": dataclasses.asdict(stored_report.report),
                        "is_stale": stored_report.is_stale,
                    },
                )
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="기본: 실제 HTTP 연결을 실행하고 종료")
    mode.add_argument("--serve", action="store_true", help="이유림의 화면 연결용 loopback 서버 유지")
    parser.add_argument("--port", type=int, default=None, help="serve 기본8000, verify 기본임의빈포트")
    parser.add_argument("--output", type=Path, default=None, help="비밀을 제외한 연결정보·실행증거 폴더")
    args = parser.parse_args()
    if any(name.startswith("HJ_") for name in os.environ):
        print("BLOCKED: HJ_* 환경설정이 있습니다. 운영 설정이 없는 별도 개발 터미널에서 실행하세요.")
        return 2
    output = (args.output or ROOT / "var" / ("syn001_serve" if args.serve else "syn001_verify")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    port = args.port if args.port is not None else (8000 if args.serve else 0)
    if not 0 <= port <= 65535:
        parser.error("포트는 0~65535 사이여야 합니다.")
    run = None
    temp = None
    try:
        if args.serve:
            db_root = ROOT / "var" / f"syn001_private_{uuid.uuid4().hex}"
            db_root.mkdir(parents=True, mode=0o700)
        else:
            temp = tempfile.TemporaryDirectory(prefix="gwonri_syn001_")
            db_root = Path(temp.name)
        write_json(
            output / "result.json", {"status": "NOT RUN", "meaning": "이번 실행 시작, 이전 PASS 재사용 금지"}
        )
        run = LocalRun(db_root, output, port)
        if args.serve:
            run.serve(db_root / "private_credentials.json")
            return 0
        result = run.verify()
        write_json(output / "result.json", result)
        print(
            json.dumps(
                {"status": result["status"], "http_requests": result["http_requests"], "output": str(output)},
                ensure_ascii=False,
            )
        )
        return 0
    except KeyboardInterrupt:
        write_json(
            output / "result.json",
            {"status": "NOT RUN", "mode": "serve", "server": "STOPPED", "frontend_qa": "NOT RUN"},
        )
        return 0
    except Exception as exc:
        # 예외 원문에는 라이브러리 요청 데이터가 섞일 수 있어 출력하지 않는다.
        result = {
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "last_step": run.last_step if run else "초기화",
        }
        write_json(output / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 1
    finally:
        if run is not None:
            run.close()
        if temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
