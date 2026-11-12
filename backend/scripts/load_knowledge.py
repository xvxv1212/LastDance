"""data/knowledge/sources.json 을 벡터 지식 DB(knowledge 스키마)에 적재한다(v0.9).

usage: HJ_KNOWLEDGE_DATABASE_URL=postgresql+psycopg://app_knowledge_loader:…@host/db \\
           python scripts/load_knowledge.py [path]
임베딩은 모델이 정해진 뒤 AI 파트가 채운다(지금은 본문·메타데이터만 적재).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.infrastructure.db.engine import build_session_factory
from app.infrastructure.knowledge import LocalKnowledgeBase
from app.infrastructure.pgvector_knowledge import load_knowledge


def main() -> None:
    url = os.environ.get("HJ_KNOWLEDGE_DATABASE_URL")
    if not url:
        raise SystemExit("환경변수 HJ_KNOWLEDGE_DATABASE_URL 이 필요합니다.")
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/knowledge/sources.json")
    local = LocalKnowledgeBase.from_json(path)
    engine, _ = build_session_factory(url)
    n = load_knowledge(engine, local.sources, local.chunks)
    print(f"적재 완료: 청크 {n}건")


if __name__ == "__main__":
    main()
