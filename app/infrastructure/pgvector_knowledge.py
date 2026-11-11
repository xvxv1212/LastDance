"""벡터 지식 DB 어댑터(v0.9 팀 결정 6 — A안): 같은 PostgreSQL 의 knowledge 스키마 + pgvector.

- KnowledgeBase 포트(인용 검증용 조회)를 구현한다 → 유스케이스 수정 없이 container 에서 교체(DIP).
- 업무 DB 트랜잭션(UnitOfWork)과 별개 접속으로 읽는다(읽기 전용, 공개 자료만).
- similar_chunks() 는 AI 파트(LangChain RAG)용 유사도 검색이다. 백엔드 유스케이스는 쓰지 않는다.
- 임베딩 값은 문자열 '[0.1,0.2,…]' 로 넘겨 pgvector 가 형 변환한다(별도 파이썬 패키지 불필요).
- v0.10: EmbeddingSpec(모델·리비전·차원·전처리)을 주면 적재·검색 때 차원과 유한값을 검사하고,
  다른 모델·리비전의 벡터가 같은 표에 섞이지 않게 막는다. 임베딩 생성(모델 실행)은 AI 파트 담당이다.
  검색 거리(distance)는 유사도일 뿐이며 사실의 정확도·신뢰도로 표시하지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.application.ports.services import KnowledgeBase
from app.domain.policies import ChunkFacts
from app.infrastructure.knowledge import KnowledgeChunk, KnowledgeSource


def _vec(values: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


@dataclass(frozen=True)
class EmbeddingSpec:
    """임베딩 설정 식별값. 질문·문서는 같은 모델·고정 리비전·전처리로 만들어야 한다."""

    model_id: str
    revision: str
    dimension: int
    query_prefix: str = ""
    passage_prefix: str = ""
    pooling: str = "mean"
    l2_normalize: bool = True
    max_tokens: int = 512

    @property
    def identifier(self) -> str:
        """knowledge.chunks.embedding_model 에 기록하는 값(모델@리비전)."""
        return f"{self.model_id}@{self.revision}"

    def format_query(self, text_: str) -> str:
        return f"{self.query_prefix}{text_}"

    def format_passage(self, text_: str) -> str:
        return f"{self.passage_prefix}{text_}"

    def validate(self, vector: list[float], what: str = "벡터") -> None:
        if len(vector) != self.dimension:
            raise ValueError(
                f"{what} 차원이 {len(vector)} 입니다. {self.identifier} 는 {self.dimension} 차원입니다."
            )
        if not all(math.isfinite(float(v)) for v in vector):
            raise ValueError(f"{what}에 유한하지 않은 값(NaN·무한대)이 있습니다.")


# 2026-11-11 작업지시서 v1.1 의 초기 검증 후보(공식 공개 설정 기준). 팀의 검증 완료 모델이라는 뜻이 아니다.
E5_SMALL_CANDIDATE = EmbeddingSpec(
    model_id="intfloat/multilingual-e5-small",
    revision="614241f622f53c4eeff9890bdc4f31cfecc418b3",
    dimension=384,
    query_prefix="query: ",
    passage_prefix="passage: ",
)


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: str
    content: str
    distance: float  # 코사인 거리(작을수록 가까움)


class PgVectorKnowledgeBase(KnowledgeBase):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def chunk_facts(self, chunk_ids: list[str]) -> dict[str, ChunkFacts]:
        if not chunk_ids:
            return {}
        sql = text(
            "SELECT c.id, c.content, c.article_ref, s.is_active, s.effective_date, s.title, s.url "
            "FROM knowledge.chunks c JOIN knowledge.sources s ON s.id = c.source_id "
            "WHERE c.id = ANY(:ids)"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"ids": list(chunk_ids)}).all()
        return {
            r.id: ChunkFacts(
                content=r.content,
                source_active=bool(r.is_active),
                effective_date=r.effective_date,
                source_title=r.title,
                source_url=r.url,
                article_ref=r.article_ref,
            )
            for r in rows
        }

    def similar_chunks(
        self,
        embedding: list[float],
        k: int = 5,
        active_only: bool = True,
        spec: EmbeddingSpec | None = None,
    ) -> list[ChunkHit]:
        """코사인 거리 기준 상위 k개(AI 검색용). 폐지된 출처는 기본 제외.

        spec 을 주면 질문 벡터의 차원·유한값을 검사하고, 같은 모델·리비전으로 만든 청크만 검색한다.
        """
        if spec is not None:
            spec.validate(embedding, "질문 벡터")
        sql = text(
            "SELECT c.id, c.content, c.embedding <=> CAST(:q AS vector) AS distance "
            "FROM knowledge.chunks c JOIN knowledge.sources s ON s.id = c.source_id "
            "WHERE c.embedding IS NOT NULL AND (s.is_active OR NOT :active_only) "
            "AND (CAST(:model AS text) IS NULL OR c.embedding_model = :model) "
            "ORDER BY distance LIMIT :k"
        )
        params = {
            "q": _vec(embedding),
            "k": int(k),
            "active_only": active_only,
            "model": spec.identifier if spec is not None else None,
        }
        with self._engine.connect() as conn:
            rows = conn.execute(sql, params).all()
        return [ChunkHit(r.id, r.content, float(r.distance)) for r in rows]


def load_knowledge(
    engine: Engine,
    sources: list[KnowledgeSource],
    chunks: list[KnowledgeChunk],
    embeddings: dict[str, list[float]] | None = None,
    embedding_model: str | None = None,
    spec: EmbeddingSpec | None = None,
) -> int:
    """출처·청크를 적재(같은 ID 는 갱신). 적재 전용 계정(app_knowledge_loader)으로 실행한다.

    spec 을 주면 벡터마다 차원·유한값을 검사하고 embedding_model 에 '모델@리비전'을 기록한다.
    이번에 적재하지 않는 청크에 다른 모델·리비전의 벡터가 남아 있으면 섞이지 않도록 거부한다.
    """
    embeddings = embeddings or {}
    missing = {c.source_id for c in chunks} - {s.id for s in sources}
    if missing:
        raise ValueError(f"출처가 없는 청크가 있습니다: {sorted(missing)}")
    if spec is not None:
        embedding_model = spec.identifier
        for chunk_id, vector in embeddings.items():
            spec.validate(vector, f"청크 {chunk_id} 벡터")
    with engine.begin() as conn:
        if spec is not None and embeddings:
            found = conn.execute(
                text(
                    "SELECT DISTINCT embedding_model FROM knowledge.chunks "
                    "WHERE embedding IS NOT NULL AND embedding_model IS DISTINCT FROM :model "
                    "AND NOT (id = ANY(:ids))"
                ),
                {"model": embedding_model, "ids": list(embeddings)},
            ).all()
            others: list[str] = [str(row[0]) for row in found]
            if others:
                raise ValueError(
                    f"다른 모델·리비전의 벡터가 이미 있습니다: {sorted(others)}. "
                    "별도 스키마·표에 적재하거나 전량 재색인하세요."
                )
        for s in sources:
            conn.execute(
                text(
                    "INSERT INTO knowledge.sources (id, source_type, title, issuer, url, version_label,"
                    " is_active, effective_date, checked_on, local_file)"
                    " VALUES (:id, :source_type, :title, :issuer, :url, :version_label, :is_active,"
                    " :effective_date, :checked_on, :local_file)"
                    " ON CONFLICT (id) DO UPDATE SET source_type = EXCLUDED.source_type,"
                    " title = EXCLUDED.title,"
                    " issuer = EXCLUDED.issuer, url = EXCLUDED.url, version_label = EXCLUDED.version_label,"
                    " is_active = EXCLUDED.is_active, effective_date = EXCLUDED.effective_date,"
                    " checked_on = EXCLUDED.checked_on, local_file = EXCLUDED.local_file, loaded_at = now()"
                ),
                {
                    "id": s.id,
                    "source_type": s.source_type,
                    "title": s.title,
                    "issuer": s.issuer,
                    "url": s.url,
                    "version_label": s.version_label,
                    "is_active": s.is_active,
                    "effective_date": s.effective_date,
                    "checked_on": s.checked_on,
                    "local_file": s.local_file,
                },
            )
        for c in chunks:
            emb = embeddings.get(c.id)
            conn.execute(
                text(
                    "INSERT INTO knowledge.chunks (id, source_id, chunk_index, article_ref, content,"
                    " embedding, embedding_model)"
                    " VALUES (:id, :source_id, :chunk_index, :article_ref, :content,"
                    " CAST(:embedding AS vector), :model)"
                    " ON CONFLICT (id) DO UPDATE SET source_id = EXCLUDED.source_id,"
                    " chunk_index = EXCLUDED.chunk_index, article_ref = EXCLUDED.article_ref,"
                    " content = EXCLUDED.content, embedding = EXCLUDED.embedding,"
                    " embedding_model = EXCLUDED.embedding_model"
                ),
                {
                    "id": c.id,
                    "source_id": c.source_id,
                    "chunk_index": c.chunk_index,
                    "article_ref": c.article_ref,
                    "content": c.content,
                    "embedding": _vec(emb) if emb is not None else None,
                    "model": embedding_model if emb is not None else None,
                },
            )
    return len(chunks)
