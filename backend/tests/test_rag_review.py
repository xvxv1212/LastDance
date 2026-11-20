
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.application.use_cases.check_session import SessionFacts
from app.application.use_cases.evidence_packet import PrepareEvidencePacket
from app.application.use_cases.rag_review import PrepareSessionRagReview, RagHit
from app.domain.entities import CheckSession, ExtractedFact
from app.domain.enums import (
    EntryType,
    FactKey,
    FactSourceKind,
    FactStatus,
    SessionStatus,
)
from app.infrastructure.citation_approval import ApprovedKnowledgeBase
from app.infrastructure.knowledge import LocalKnowledgeBase

DATA = Path(__file__).resolve().parents[1] / "data" / "knowledge"
CHUNK_ID = "kca-1372-phone-20261112#method"


def setup_case():
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    now = datetime.now(UTC)

    session = CheckSession(
        session_id,
        user_id,
        EntryType.REFUND_CONCERN,
        "합성 필라테스 이용권 해지 상담 준비",
        SessionStatus.FACTS_CONFIRMED,
        now,
        now,
        facts_confirmed_at=now,
    )

    facts = [
        ExtractedFact(
            uuid.uuid4(),
            session_id,
            FactKey.PAYMENT_AMOUNT,
            "600000원",
            FactSourceKind.STATEMENT,
            FactStatus.CONFIRMED,
            now,
            now,
            confirmed_by=user_id,
            confirmed_at=now,
        ),
        ExtractedFact(
            uuid.uuid4(),
            session_id,
            FactKey.CONTRACT_DATE,
            None,
            FactSourceKind.UNKNOWN,
            FactStatus.ACKNOWLEDGED_UNKNOWN,
            now,
            now,
            confirmed_by=user_id,
            confirmed_at=now,
        ),
    ]

    class Reader:
        def execute(self, actor, sid):
            session.ensure_owned_by(actor)
            assert sid == session_id
            return SessionFacts(session, facts)

    raw = LocalKnowledgeBase.from_json(DATA / "sources.json")
    approved = ApprovedKnowledgeBase.from_file(
        raw, DATA / "approvals.json"
    )
    packet = PrepareEvidencePacket(approved, {})

    return user_id, session_id, Reader(), packet


def test_search_not_configured():
    uid, sid, reader, packet = setup_case()

    result = PrepareSessionRagReview(reader, packet).execute(
        user_id=uid, session_id=sid
    )

    assert result.search_status == "NOT_CONFIGURED"
    assert "SEARCH_NOT_CONFIGURED" in result.blocked_reasons
    assert result.packet is None
    assert not result.generation_allowed


def test_approved_candidate_is_review_only():
    uid, sid, reader, packet = setup_case()

    # 대역은 연결 시험용이다. 실제 E5 검색을 의미하지 않는다.
    def search(query: str) -> list[RagHit]:
        return [RagHit(CHUNK_ID, 0.1)]

    result = PrepareSessionRagReview(
        reader, packet, search
    ).execute(
        user_id=uid,
        session_id=sid,
        required_fact_keys=(FactKey.PAYMENT_AMOUNT.value,),
    )

    assert result.search_status == "REVIEW_ONLY"
    assert result.candidate_ids == (CHUNK_ID,)
    assert not result.generation_allowed

    body = json.loads(result.packet.review_json)
    assert body["purpose"] == "RAG_REVIEW_ONLY"
    assert body["evidence"][0]["id"] == CHUNK_ID
    assert any(
        f["state"] == "UNKNOWN" and f["value"] is None
        for f in body["facts"]
    )


def test_no_matching_evidence():
    uid, sid, reader, packet = setup_case()

    result = PrepareSessionRagReview(
        reader, packet, lambda query: []
    ).execute(user_id=uid, session_id=sid)

    assert result.search_status == "NO_HITS"
    assert "NO_SEARCH_RESULTS" in result.blocked_reasons
    assert "NO_APPROVED_EVIDENCE" in result.blocked_reasons
    assert not result.generation_allowed
