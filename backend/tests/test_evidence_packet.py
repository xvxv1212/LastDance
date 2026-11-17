"""승인 경계, 연결 제한, 사용자 사실과 전체 전송 예산 회귀 검증."""
import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.application.ports.services import ApprovedEvidenceReader
from app.application.use_cases.evidence_packet import PrepareEvidencePacket
from app.domain.entities import ExtractedFact
from app.domain.enums import FactKey, FactSourceKind, FactStatus
from app.domain.policies import ChunkFacts, PersonalInfoMasker
from app.infrastructure.citation_approval import ApprovedKnowledgeBase
from app.infrastructure.knowledge import LocalKnowledgeBase

DATA = Path(__file__).resolve().parents[1] / "data" / "knowledge"


@pytest.mark.parametrize("text,expected", [
    ("계약일: 2025-12-18", "계약일: 2025-12-18"),
    ("시행일 2024-02-29", "시행일 2024-02-29"),
    ("날짜 2025-02-29", "날짜 [계좌번호]"),
    ("계좌번호 2025-12-18", "계좌번호 [계좌번호]"),
    ("2025-12-18", "[계좌번호]"),
    ("계좌 123-456-7890", "계좌 [계좌번호]"),
    ("시행일: 2025-12-18 계좌 123-456-7890", "시행일: 2025-12-18 계좌 [계좌번호]"),
])
def test_date_context_preserves_masking(text, expected):
    assert PersonalInfoMasker().mask(text) == expected


class Reader(ApprovedEvidenceReader):
    """연결 그래프 검증용 대역. 실제 승인 검증은 아래 별도 통합시험으로 확인."""
    def evaluate(self, ids):
        return ({cid: ChunkFacts("공식 자료", True, date(2025, 12, 18)) for cid in ids if cid != "bad"},
                {cid: ["TEAM_APPROVAL_MISSING"] if cid == "bad" else [] for cid in ids})

    def chunk_facts(self, ids):
        return self.evaluate(ids)[0]


def arguments():
    sid, uid = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    key = next(iter(FactKey))
    fact = ExtractedFact(uuid.uuid4(), sid, key, "사용자 확인 값", FactSourceKind.STATEMENT,
                         FactStatus.CONFIRMED, now, now, confirmed_by=uid, confirmed_at=now)
    return {"session_id": sid, "user_id": uid, "text": "계약일 2025-12-18", "facts": [fact],
            "direct_ids": ["a"], "required_fact_keys": (key.value,)}


def test_approved_link_cycle_and_date_projection():
    result = PrepareEvidencePacket(Reader(), {"a": ("b",), "b": ("a",)}).execute(**arguments())
    items = json.loads(result.review_json)["evidence"]
    assert [x["id"] for x in items] == ["a", "b"]
    assert items[1]["included_via"] == "a"
    assert items[0]["effective_date"] == "2025-12-18"
    assert not result.generation_allowed


def test_unapproved_required_link_blocks():
    result = PrepareEvidencePacket(Reader(), {"a": ("bad",)}).execute(**arguments())
    assert "EVIDENCE_BLOCKED:bad:TEAM_APPROVAL_MISSING" in result.blocked_reasons
    assert len(json.loads(result.review_json)["evidence"]) == 1


@pytest.mark.parametrize("limits,code", [
    ({"max_depth": 0}, "LINK_DEPTH_EXCEEDED:b"),
    ({"max_records": 1}, "EVIDENCE_COUNT_EXCEEDED"),
    ({"max_user_chars": 1}, "USER_INPUT_TOO_LARGE"),
    ({"max_evidence_chars": 1}, "EVIDENCE_INPUT_TOO_LARGE"),
    ({"max_total_chars": 1}, "TOTAL_INPUT_TOO_LARGE"),
])
def test_limits_fail_closed(limits, code):
    result = PrepareEvidencePacket(Reader(), {"a": ("b",)}, **limits).execute(**arguments())
    assert code in result.blocked_reasons
    assert not result.generation_allowed


def test_unknown_is_not_a_confirmed_value():
    args = arguments()
    args["facts"] = [replace(args["facts"][0], source_kind=FactSourceKind.UNKNOWN,
                              value_text=None, status=FactStatus.ACKNOWLEDGED_UNKNOWN)]
    result = PrepareEvidencePacket(Reader(), {}).execute(**args)
    fact = json.loads(result.review_json)["facts"][0]
    assert fact["state"] == "UNKNOWN" and fact["value"] is None
    assert "CASE_FACTS_NOT_CONFIRMED" in result.blocked_reasons


def test_foreign_session_facts_are_excluded():
    args = arguments()
    args["facts"] = [replace(args["facts"][0], check_session_id=uuid.uuid4())]
    result = PrepareEvidencePacket(Reader(), {}).execute(**args)
    assert "FACT_SESSION_MISMATCH" in result.blocked_reasons
    assert not json.loads(result.review_json)["facts"]


def test_real_approval_receipt_is_required():
    raw = LocalKnowledgeBase.from_json(DATA / "sources.json")
    args = arguments()
    args["direct_ids"] = [raw.chunks[0].id]
    reader = ApprovedKnowledgeBase.from_file(raw, DATA / "approvals.json")
    good = PrepareEvidencePacket(reader, {}).execute(**args)
    assert len(json.loads(good.review_json)["evidence"]) == 1
    manifest = json.loads((DATA / "approvals.json").read_text(encoding="utf-8"))
    manifest["chunks"][raw.chunks[0].id]["team_approval"]["approved_by"] = "forged"
    bad = PrepareEvidencePacket(ApprovedKnowledgeBase(raw, manifest, DATA), {}).execute(**args)
    assert not json.loads(bad.review_json)["evidence"]
    assert any("APPROVAL_RECEIPT_MISMATCH" in reason for reason in bad.blocked_reasons)


def test_raw_knowledge_reader_rejected():
    with pytest.raises(TypeError):
        PrepareEvidencePacket(LocalKnowledgeBase([], []), {})
