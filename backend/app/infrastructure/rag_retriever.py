
"""S07 RAG 검토용 실제 pgvector 검색 어댑터.

임베딩 생성기는 외부에서 주입한다.
검색 결과는 후보일 뿐이며 공식 근거 인용 승인이 아니다.
"""

from __future__ import annotations

from collections.abc import Callable

from app.application.use_cases.rag_review import RagHit
from app.infrastructure.pgvector_knowledge import (
    E5_SMALL_CANDIDATE,
    EmbeddingSpec,
    PgVectorKnowledgeBase,
)


class PgVectorRagRetriever:
    def __init__(
        self,
        knowledge: PgVectorKnowledgeBase,
        embed_query: Callable[[str], list[float]],
        *,
        spec: EmbeddingSpec = E5_SMALL_CANDIDATE,
        top_k: int = 3,
    ) -> None:
        if not 1 <= top_k <= 5:
            raise ValueError("검색 후보 수는 1~5개여야 합니다.")

        self.knowledge = knowledge
        self.embed_query = embed_query
        self.spec = spec
        self.top_k = top_k

    def __call__(self, query: str) -> list[RagHit]:
        if not query.strip():
            raise ValueError("빈 검색 질문은 허용하지 않습니다.")

        # 기존 고정 E5 설정으로 질문을 전처리한다.
        prepared = self.spec.format_query(query)
        vector = self.embed_query(prepared)

        # 차원·NaN·무한대 차단
        self.spec.validate(vector, "질문 벡터")

        # 실제 pgvector 검색. 검색 후보를 인용 승인으로 승격하지 않는다.
        hits = self.knowledge.similar_chunks(
            embedding=vector,
            k=self.top_k,
            active_only=True,
            spec=self.spec,
        )

        return [
            RagHit(chunk_id=hit.chunk_id, distance=hit.distance)
            for hit in hits
        ]
