
"""로컬 전용: 실제 S07 HTTP -> E5 -> pgvector -> 인용 보류."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.infrastructure.citation_approval import ApprovedKnowledgeBase
from app.infrastructure.pgvector_knowledge import PgVectorKnowledgeBase
from app.infrastructure.rag_retriever import PgVectorRagRetriever
from tests.conftest import agree_all

pytestmark = pytest.mark.skipif(
    os.environ.get("GWONRI_LOCAL_RAG_HTTP_TEST") != "1",
    reason="로컬 E5 + pgvector 명시적 시험 전용",
)

E5_CODE = """
import json
import sys
from sentence_transformers import SentenceTransformer

model = SentenceTransformer(
    "intfloat/multilingual-e5-small",
    revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
    device="cpu",
    trust_remote_code=False,
)
model.max_seq_length = 512
vector = model.encode(
    sys.stdin.read().strip(),
    normalize_embeddings=True,
)
print(json.dumps(vector.tolist()))
"""


def encode_query(query: str) -> list[float]:
    # 로컬 시험 전용 E5 Python 실행. shell을 사용하지 않고 질문은 stdin으로 전달한다.
    result = subprocess.run(  # noqa: S603
        [os.environ["GWONRI_E5PY"], "-X", "utf8", "-c", E5_CODE],
        input=query,
        text=True,
        capture_output=True,
        check=True,
        timeout=240,
    )
    return json.loads(result.stdout)


def test_http_s07_real_e5_pgvector_review(ctx):
    e5py = Path(os.environ["GWONRI_E5PY"])
    assert e5py.is_file()

    # 시험 DB만 사용한다. 운영 DB 접속정보를 받지 않는다.
    url = URL.create(
        "postgresql+psycopg",
        username="rag_test",
        password=os.environ["GWONRI_LOCAL_RAG_PW"],
        host="127.0.0.1",
        port=55432,
        database="rag_test",
    )
    engine = create_engine(
        url,
        connect_args={
            "connect_timeout": 5,
            "options": "-c default_transaction_read_only=on",
        },
    )

    try:
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT current_database()")
            ).scalar_one() == "rag_test"
            assert conn.execute(
                text("SELECT COUNT(*) FROM rag_smoke_20261120.chunks")
            ).scalar_one() == 3

        knowledge = PgVectorKnowledgeBase(engine)
        approved = ApprovedKnowledgeBase.from_file(
            knowledge, Path("data/knowledge/approvals.json")
        )

        # 합성 문서는 승인된 공식 근거가 아니다.
        synthetic_ids = ["SYN-PILATES", "SYN-CAR", "SYN-FOOD"]
        allowed, decisions = approved.evaluate(synthetic_ids)
        assert not allowed
        assert all(decisions[cid] for cid in synthetic_ids)

        # 시험 컨테이너에만 검색기를 주입한다.
        ctx.container.knowledge = approved
        ctx.container._rag_retriever = PgVectorRagRetriever(
            knowledge, encode_query, top_k=3
        )

        agree_all(ctx, ctx.user_a)

        created = ctx.client.post(
            "/api/v1/check-sessions",
            headers=ctx.auth(ctx.user_a),
            json={
                "entry_type": "REFUND_CONCERN",
                "situation_text": (
                    "합성 필라테스 회원권 중도 해지와 환불 상담 준비"
                ),
            },
        )
        assert created.status_code == 201, created.text
        sid = created.json()["id"]

        known = [
            ("CONTRACT_PERIOD", "6개월"),
            ("PAYMENT_AMOUNT", "600000원"),
            ("TOTAL_SESSIONS", "12회"),
            ("USED_SESSIONS", "4회"),
        ]
        unknown = ["CONTRACT_DATE", "CANCEL_REQUEST_DATE", "OTHER"]

        registered = ctx.client.post(
            f"/api/v1/check-sessions/{sid}/facts",
            headers=ctx.svc(ctx.user_a),
            json={
                "facts": [
                    {
                        "fact_key": key,
                        "value_text": value,
                        "source_kind": "STATEMENT",
                    }
                    for key, value in known
                ] + [
                    {"fact_key": key, "source_kind": "UNKNOWN"}
                    for key in unknown
                ],
            },
        )
        assert registered.status_code == 201, registered.text

        fetched = ctx.client.get(
            f"/api/v1/check-sessions/{sid}/facts",
            headers=ctx.auth(ctx.user_a),
        )
        assert fetched.status_code == 200

        for fact in fetched.json()["facts"]:
            action = (
                "ACKNOWLEDGE_UNKNOWN"
                if fact["source_kind"] == "UNKNOWN"
                else "CONFIRM"
            )
            confirmed = ctx.client.post(
                f"/api/v1/facts/{fact['id']}/confirmation",
                headers=ctx.auth(ctx.user_a),
                json={"action": action},
            )
            assert confirmed.status_code == 200

        endpoint = f"/api/v1/check-sessions/{sid}/rag-review"

        # 다른 사용자의 저장된 사실은 검색할 수 없다.
        foreign = ctx.client.post(
            endpoint, headers=ctx.auth(ctx.user_b)
        )
        assert foreign.status_code == 404

        response = ctx.client.post(
            endpoint, headers=ctx.auth(ctx.user_a)
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "search_status": "REVIEW_ONLY",
            "candidate_count": 3,
            "generation_allowed": False,
        }

        assert ctx.model.calls == []

        print("HTTP_S07_REAL_E5_PGVECTOR: PASS")
        print("SYNTHETIC_APPROVED_EVIDENCE: 0")
        print("MODEL_CALLS: 0")
    finally:
        engine.dispose()
