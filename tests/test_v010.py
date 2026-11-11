"""v0.10(2026-11-11 작업지시서 v1.1) 회귀 테스트: confidence 계약 정리, 임베딩 설정 연결."""

from __future__ import annotations

import math
import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.infrastructure.db import models as orm
from app.infrastructure.knowledge import KnowledgeChunk, KnowledgeSource
from app.infrastructure.pgvector_knowledge import (
    E5_SMALL_CANDIDATE,
    EmbeddingSpec,
    PgVectorKnowledgeBase,
    load_knowledge,
)
from tests.conftest import Ctx, agree_all


def _session(ctx: Ctx) -> str:
    agree_all(ctx, ctx.user_a)
    r = ctx.client.post(
        "/api/v1/check-sessions",
        headers=ctx.auth(ctx.user_a),
        json={"entry_type": "REFUND_CONCERN", "situation_text": "합성 상황 설명입니다(시험용)."},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ---------------------------------------------------------------------------
# confidence: 받지도 저장하지도 않는다
# ---------------------------------------------------------------------------
def test_facts_are_registered_without_confidence(ctx: Ctx) -> None:
    sid = _session(ctx)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {"fact_key": "BUSINESS_NAME", "value_text": "가나필라테스", "source_kind": "STATEMENT"},
                {"fact_key": "BUSINESS_RESPONSE", "source_kind": "UNKNOWN"},  # 값 없이 미확인으로
            ]
        },
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["ids"]) == 2


@pytest.mark.parametrize("value", [0.93, 0, 1, None])
def test_fact_with_confidence_field_is_rejected(ctx: Ctx, value: float | None) -> None:
    """기존 호출자가 confidence 를 보내면(값이 null 이어도) 422 — 조용히 버리지 않는다."""
    sid = _session(ctx)
    r = ctx.client.post(
        f"/api/v1/check-sessions/{sid}/facts",
        headers=ctx.svc(ctx.user_a),
        json={
            "facts": [
                {
                    "fact_key": "BUSINESS_NAME",
                    "value_text": "가",
                    "source_kind": "STATEMENT",
                    "confidence": value,
                }
            ]
        },
    )
    assert r.status_code == 422, r.text


def test_extracted_facts_table_has_no_confidence_column(ctx: Ctx) -> None:
    with ctx.factory() as s:
        columns = {c["name"] for c in inspect(s.get_bind()).get_columns("extracted_facts")}
    assert "confidence" not in columns
    assert "confidence" not in orm.ExtractedFactModel.__table__.columns
    assert "ai_value_text" in columns and "source_kind" in columns  # 근거·AI 원래 값은 그대로


# ---------------------------------------------------------------------------
# 임베딩 설정: 모델·리비전·차원·전처리 식별값과 차원 검사
# ---------------------------------------------------------------------------
def test_embedding_spec_candidate_identifies_model_revision_and_preprocessing() -> None:
    spec = E5_SMALL_CANDIDATE
    assert spec.dimension == 384
    assert spec.identifier == "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert len(spec.identifier) <= 100  # knowledge.chunks.embedding_model VARCHAR(100)
    assert spec.format_query("환불 기준") == "query: 환불 기준"
    assert spec.format_passage("환급 기준 본문") == "passage: 환급 기준 본문"
    spec.validate([0.0] * 384)
    with pytest.raises(ValueError, match="차원"):
        spec.validate([0.0] * 383)
    with pytest.raises(ValueError, match="유한"):
        spec.validate([math.nan] + [0.0] * 383)
    with pytest.raises(ValueError, match="유한"):
        spec.validate([math.inf] + [0.0] * 383)


def _unit(index: int, dim: int = 384, mix: float = 0.0, other: int = 0) -> list[float]:
    """시험용 합성 벡터(모델이 만든 임베딩이 아니다): index 축 단위 벡터에 other 축을 조금 섞는다."""
    v = [0.0] * dim
    v[index] = 1.0
    v[other] += mix
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def test_pgvector_load_and_search_enforce_embedding_spec(ctx: Ctx) -> None:
    url = os.environ.get("HJ_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("pgvector 시험은 PostgreSQL 에서만")
    engine = ctx.factory.kw["bind"]
    ddl = (Path(__file__).resolve().parents[1] / "db" / "knowledge_schema.sql").read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
            cur.execute(ddl)
        raw.commit()
    finally:
        raw.close()
    spec = E5_SMALL_CANDIDATE
    src = KnowledgeSource(
        "std",
        "DISPUTE_STANDARD",
        "시험 기준(합성)",
        "시험",
        "https://example.test/",
        "t",
        True,
        date(2025, 1, 1),
    )
    chunks = [KnowledgeChunk(f"std#{i}", "std", i, f"합성 청크 {i}") for i in range(3)]
    emb = {f"std#{i}": _unit(i) for i in range(3)}

    # 차원이 다른 벡터·유한하지 않은 값은 적재 전에 거부(아무것도 저장되지 않음)
    with pytest.raises(ValueError, match="차원"):
        load_knowledge(engine, [src], chunks, {**emb, "std#2": [1.0, 0.0, 0.0]}, spec=spec)
    with pytest.raises(ValueError, match="유한"):
        load_knowledge(engine, [src], chunks, {**emb, "std#2": [math.nan] * 384}, spec=spec)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM knowledge.chunks").scalar() == 0

    assert load_knowledge(engine, [src], chunks, emb, spec=spec) == 3
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT DISTINCT embedding_model, vector_dims(embedding) FROM knowledge.chunks"
        ).all()
    assert [tuple(r) for r in rows] == [(spec.identifier, 384)]

    kb = PgVectorKnowledgeBase(engine)
    hits = kb.similar_chunks(_unit(1, mix=0.2, other=2), k=3, spec=spec)
    assert [h.chunk_id for h in hits] == ["std#1", "std#2", "std#0"]
    assert all(math.isfinite(h.distance) for h in hits)
    with pytest.raises(ValueError, match="차원"):
        kb.similar_chunks([1.0, 0.0, 0.0], spec=spec)

    # 같은 차원이어도 다른 리비전의 벡터는 같은 표에 섞지 못한다
    other = EmbeddingSpec(
        spec.model_id, "0000000000000000000000000000000000000000", 384, "query: ", "passage: "
    )
    extra = [KnowledgeChunk("std#9", "std", 9, "다른 리비전 청크")]
    with pytest.raises(ValueError, match="다른 모델"):
        load_knowledge(engine, [src], extra, {"std#9": _unit(9)}, spec=other)
    assert kb.similar_chunks(_unit(1), k=5, spec=other) == []  # 다른 리비전으로는 검색되지 않는다
