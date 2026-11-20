
from unittest.mock import Mock

import pytest

from app.infrastructure.pgvector_knowledge import (
    E5_SMALL_CANDIDATE,
    ChunkHit,
)
from app.infrastructure.rag_retriever import PgVectorRagRetriever


def test_query_prefix_and_search_connection():
    captured = []

    def encode_query(text: str) -> list[float]:
        captured.append(text)
        return [0.0] * 383 + [1.0]

    knowledge = Mock()
    knowledge.similar_chunks.return_value = [
        ChunkHit("SYN-PILATES", "합성 필라테스 문서", 0.064),
    ]

    retriever = PgVectorRagRetriever(knowledge, encode_query)
    result = retriever("필라테스 회원권 환불")

    assert captured == ["query: 필라테스 회원권 환불"]
    assert result[0].chunk_id == "SYN-PILATES"
    assert result[0].distance == 0.064

    kwargs = knowledge.similar_chunks.call_args.kwargs
    assert kwargs["spec"].identifier == E5_SMALL_CANDIDATE.identifier
    assert kwargs["k"] == 3
    assert kwargs["active_only"] is True


def test_wrong_embedding_dimension_blocked():
    knowledge = Mock()

    retriever = PgVectorRagRetriever(
        knowledge,
        lambda text: [0.0, 1.0, 0.0],
    )

    with pytest.raises(ValueError, match="차원"):
        retriever("필라테스 환불")

    knowledge.similar_chunks.assert_not_called()


def test_search_candidate_limit():
    knowledge = Mock()

    with pytest.raises(ValueError):
        PgVectorRagRetriever(
            knowledge,
            lambda text: [0.0] * 384,
            top_k=100,
        )
