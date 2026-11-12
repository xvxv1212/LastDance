"""업무 DB 밖의 저장소 어댑터: 벡터 지식 DB(공식 출처·청크), 지원기관 목록(설정 파일).

v0.6 (2026.11.06 DB 멘토링 피드백 권장안)
- 구 official_sources·source_chunks(7·8번) → 벡터 지식 DB. 이 파일의 LocalKnowledgeBase 는
  '같은 포트를 만족하는 가장 단순한 구현'(JSON 파일)이다. 벡터 DB 제품이 정해지면
  ChromaKnowledgeBase 등 새 어댑터를 추가하고 container 에서 교체한다(유스케이스 수정 없음).
- 구 support_agencies(17번) → data/agencies.json 을 읽는 JsonAgencyDirectory.

지식 DB 메타데이터(각 청크가 반드시 가져야 하는 값) — 벡터 DB 제품을 바꿔도 유지:
  chunk_id, source_id, title, issuer, url, source_type, version_label,
  effective_date(시행일), checked_on(확인일), is_active(폐지 여부), article_ref(조항), local_file(원문 파일)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.application.ports.services import AgencyDirectory, KnowledgeBase
from app.domain.entities import SupportAgency
from app.domain.policies import ChunkFacts


@dataclass(frozen=True)
class KnowledgeSource:
    """공식 출처 1건의 메타데이터(구 official_sources 의 컬럼과 같은 의미)."""

    id: str
    source_type: str
    title: str
    issuer: str
    url: str
    version_label: str
    is_active: bool = True
    effective_date: date | None = None
    checked_on: date | None = None
    local_file: str | None = None  # 원문 사본 경로(로컬 저장, 피드백 7번)


@dataclass(frozen=True)
class KnowledgeChunk:
    """검색 단위 청크(구 source_chunks). 임베딩 벡터는 벡터 DB 제품이 따로 보관한다."""

    id: str
    source_id: str
    chunk_index: int
    content: str
    article_ref: str | None = None


def _date(v: Any) -> date | None:
    return date.fromisoformat(str(v)) if v else None


class LocalKnowledgeBase(KnowledgeBase):
    """메모리·JSON 파일 기반 지식 DB(개발·테스트용).

    파일 형식: {"sources": [KnowledgeSource...], "chunks": [KnowledgeChunk...]}
    """

    def __init__(self, sources: list[KnowledgeSource], chunks: list[KnowledgeChunk]) -> None:
        self._sources = {s.id: s for s in sources}
        missing = {c.source_id for c in chunks} - self._sources.keys()
        if missing:  # 출처 없는 청크는 인용 검증을 할 수 없으므로 적재 단계에서 막는다
            raise ValueError(f"출처가 없는 청크가 있습니다: {sorted(missing)}")
        self._chunks = {c.id: c for c in chunks}

    @classmethod
    def from_json(cls, path: Path) -> LocalKnowledgeBase:
        if not path.exists():
            return cls([], [])
        raw = json.loads(path.read_text(encoding="utf-8"))
        sources = [
            KnowledgeSource(
                id=str(s["id"]),
                source_type=s["source_type"],
                title=s["title"],
                issuer=s["issuer"],
                url=s["url"],
                version_label=s["version_label"],
                is_active=bool(s.get("is_active", True)),
                effective_date=_date(s.get("effective_date")),
                checked_on=_date(s.get("checked_on")),
                local_file=s.get("local_file"),
            )
            for s in raw.get("sources", [])
        ]
        chunks = [
            KnowledgeChunk(
                id=str(c["id"]),
                source_id=str(c["source_id"]),
                chunk_index=int(c["chunk_index"]),
                content=c["content"],
                article_ref=c.get("article_ref"),
            )
            for c in raw.get("chunks", [])
        ]
        return cls(sources, chunks)

    @property
    def sources(self) -> list[KnowledgeSource]:
        return list(self._sources.values())

    @property
    def chunks(self) -> list[KnowledgeChunk]:
        return list(self._chunks.values())

    def chunk_facts(self, chunk_ids: list[str]) -> dict[str, ChunkFacts]:
        out: dict[str, ChunkFacts] = {}
        for cid in chunk_ids:
            chunk = self._chunks.get(cid)
            if chunk is None:
                continue
            src = self._sources[chunk.source_id]
            out[cid] = ChunkFacts(
                content=chunk.content,
                source_active=src.is_active,
                effective_date=src.effective_date,
                source_title=src.title,
                source_url=src.url,
                article_ref=chunk.article_ref,
                source_id=src.id,
                source_version=src.version_label,
            )
        return out


class JsonAgencyDirectory(AgencyDirectory):
    """data/agencies.json 을 읽는 지원기관 목록. 기관 정보 변경은 파일 수정 + 배포로 반영한다."""

    def __init__(self, agencies: list[SupportAgency]) -> None:
        self._by_code = {a.code: a for a in agencies}

    @classmethod
    def from_json(cls, path: Path) -> JsonAgencyDirectory:
        if not path.exists():
            return cls([])
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            [
                SupportAgency(
                    code=a["code"],
                    name=a["name"],
                    agency_type=a["agency_type"],
                    url=a["url"],
                    is_active=bool(a.get("is_active", True)),
                    phone=a.get("phone"),
                    description=a.get("description"),
                    eligibility=a.get("eligibility"),
                    last_verified_on=_date(a.get("last_verified_on")),
                )
                for a in raw.get("agencies", [])
            ]
        )

    def get(self, code: str) -> SupportAgency | None:
        return self._by_code.get(code)
