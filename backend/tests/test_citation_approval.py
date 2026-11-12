"""공식 근거 승인 대조: 서버 본문일치와 팀 인용 승인을 분리한다."""

from __future__ import annotations

import copy
import dataclasses
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.enums import CitationCheck
from app.domain.policies import CitationVerifier
from app.infrastructure.citation_approval import ApprovedKnowledgeBase
from app.infrastructure.knowledge import LocalKnowledgeBase
from app.main import build_container
from tests.conftest import Ctx
from tests.test_v011 import _confirmed_session

DATA = Path(__file__).resolve().parents[1] / "data" / "knowledge"
API = "/api/v1"


def _copied_package(tmp_path: Path) -> tuple[LocalKnowledgeBase, Path]:
    target = tmp_path / "knowledge"
    shutil.copytree(DATA, target)
    return LocalKnowledgeBase.from_json(target / "sources.json"), target


def test_original_quote_can_be_server_verified_but_unapproved_citation_is_withheld(ctx: Ctx) -> None:
    """핵심 회귀: 청크 실재·인용문 일치만으로 사용자 보고서에 인용하지 않는다."""
    raw = ctx.container.knowledge
    chunk = raw.chunk_facts([ctx.chunk_id])[ctx.chunk_id]
    assert CitationVerifier().check(chunk, chunk.content, date(2026, 11, 12)) == CitationCheck.VERIFIED
    ctx.container.knowledge = ApprovedKnowledgeBase(raw, {}, DATA)
    sid, fid = _confirmed_session(ctx)
    response = ctx.client.post(
        f"{API}/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "확인된 사실과 추가 확인이 필요한 항목을 정리했습니다.",
            "items": [
                {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": fid},
                {
                    "category": "CAUTION",
                    "content": "공식 자료에 대한 대조와 팀 인용 승인이 필요합니다.",
                    "citations": [{"source_chunk_id": ctx.chunk_id, "quoted_text": chunk.content}],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["held_items"] == 1
    detail = ctx.client.get(f"{API}/reports/{response.json()['id']}", headers=ctx.auth(ctx.user_a)).json()
    caution = next(item for item in detail["items"] if item["category"] == "CAUTION")
    assert caution["hold_reason"] == "CITATION_FAILED"
    assert caution["citations"][0]["check_result"] == "CHUNK_NOT_FOUND"


def test_real_kca_phone_excerpt_approval_reaches_report_without_legal_conclusion(ctx: Ctx) -> None:
    """별도 시나리오: 실제 공식 연락 안내 1개만 승인 기록에 연결해 인용한다."""
    raw = LocalKnowledgeBase.from_json(DATA / "sources.json")
    ctx.container.knowledge = ApprovedKnowledgeBase.from_file(raw, DATA / "approvals.json")
    ctx.clock.current = datetime.now(UTC)
    chunk = raw.chunks[0]
    sid, fid = _confirmed_session(ctx)
    response = ctx.client.post(
        f"{API}/check-sessions/{sid}/reports",
        headers=ctx.svc(ctx.user_a),
        json={
            "summary_text": "확인된 사실과 상담 연락 정보를 정리했습니다.",
            "items": [
                {"category": "CONFIRMED_FACT", "content": "결제 600,000원", "fact_id": fid},
                {
                    "category": "CAUTION",
                    "content": "한국소비자원에서 안내하는 소비자 전화상담 연결 정보입니다.",
                    "citations": [{"source_chunk_id": chunk.id, "quoted_text": chunk.content}],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["held_items"] == 0
    detail = ctx.client.get(f"{API}/reports/{response.json()['id']}", headers=ctx.auth(ctx.user_a)).json()
    caution = next(item for item in detail["items"] if item["category"] == "CAUTION")
    citation = caution["citations"][0]
    assert caution["hold_reason"] is None and citation["check_result"] == "VERIFIED"
    assert citation["source_url"] == raw.sources[0].url
    assert citation["effective_date"] is None  # 기관 안내에 가짜 시행일을 붙이지 않는다.
    assert detail["status"] == "DRAFT"  # 근거 승인을 사용자 보고서 승인으로 바꾸지 않는다.


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ("body", "CONTENT_HASH_MISMATCH"),
        ("version", "SOURCE_VERSION_MISMATCH"),
        ("url", "SOURCE_URL_MISMATCH"),
        ("source_id", "SOURCE_ID_MISMATCH"),
    ],
)
def test_changed_source_identity_or_body_is_held(tmp_path: Path, changed: str, reason: str) -> None:
    raw, target = _copied_package(tmp_path)
    source, chunk = raw.sources[0], raw.chunks[0]
    if changed == "body":
        chunk = dataclasses.replace(chunk, content=chunk.content + " 변경")
    elif changed == "version":
        source = dataclasses.replace(source, version_label=source.version_label + "-changed")
    elif changed == "url":
        source = dataclasses.replace(source, url="https://www.kca.go.kr/changed")
    else:
        source = dataclasses.replace(source, id=source.id + "-changed")
        chunk = dataclasses.replace(chunk, source_id=source.id)
    approved = ApprovedKnowledgeBase.from_file(
        LocalKnowledgeBase([source], [chunk]), target / "approvals.json"
    )
    allowed, decisions = approved.evaluate([chunk.id])
    assert allowed == {} and reason in decisions[chunk.id]


@pytest.mark.parametrize("kind", ["original", "receipt", "revoked", "receipt_binding"])
def test_broken_original_or_approval_evidence_is_held(tmp_path: Path, kind: str) -> None:
    raw, target = _copied_package(tmp_path)
    manifest = json.loads((target / "approvals.json").read_text())
    chunk_id = raw.chunks[0].id
    record = manifest["chunks"][chunk_id]
    if kind == "original":
        (target / record["original_comparison"]["excerpt_file"]).write_text("원문 사본 변형")
    elif kind == "receipt":
        (target / record["team_approval"]["record_file"]).unlink()
    elif kind == "revoked":
        record["team_approval"]["approved"] = False
    else:
        record["team_approval"]["source_version"] = "다른 버전에 대한 승인"
    approved = ApprovedKnowledgeBase(raw, manifest, target)
    allowed, decisions = approved.evaluate([chunk_id])
    assert allowed == {} and decisions[chunk_id]


def test_fabricated_manifest_boolean_without_receipt_binding_is_rejected(tmp_path: Path) -> None:
    raw, target = _copied_package(tmp_path)
    manifest = json.loads((target / "approvals.json").read_text())
    chunk_id = raw.chunks[0].id
    wrong = copy.deepcopy(manifest["chunks"][chunk_id]["team_approval"])
    wrong["approved_by"] = "승인되지 않은 작성자"
    manifest["chunks"][chunk_id]["team_approval"] = wrong
    allowed, decisions = ApprovedKnowledgeBase(raw, manifest, target).evaluate([chunk_id])
    assert allowed == {} and "APPROVAL_RECEIPT_MISMATCH" in decisions[chunk_id]


def test_production_container_enforces_gate_and_missing_manifest_holds(tmp_path: Path) -> None:
    raw, target = _copied_package(tmp_path)
    config = Settings(
        database_url=f"sqlite:///{tmp_path / 'unused_business.sqlite3'}",
        jwt_secret="synthetic-secret-for-approval-composition-test",
        storage_dir=tmp_path / "files",
        knowledge_path=target / "sources.json",
        knowledge_approval_path=target / "approvals.json",
    )
    container = build_container(config)
    assert isinstance(container.knowledge, ApprovedKnowledgeBase)
    assert raw.chunks[0].id in container.knowledge.chunk_facts([raw.chunks[0].id])
    missing = build_container(dataclasses.replace(config, knowledge_approval_path=target / "missing.json"))
    assert missing.knowledge.chunk_facts([raw.chunks[0].id]) == {}
