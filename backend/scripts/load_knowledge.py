"""data/knowledge/sources.json 을 벡터 지식 DB(knowledge 스키마)에 적재한다(v0.9).

usage: HJ_KNOWLEDGE_DATABASE_URL=postgresql+psycopg://app_knowledge_loader:…@host/db \\
           python scripts/load_knowledge.py [path]
임베딩은 모델이 정해진 뒤 AI 파트가 채운다(지금은 본문·메타데이터만 적재).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.infrastructure.citation_approval import ApprovedKnowledgeBase
from app.infrastructure.db.engine import build_session_factory
from app.infrastructure.knowledge import LocalKnowledgeBase
from app.infrastructure.pgvector_knowledge import load_knowledge


def main() -> None:
    parser = argparse.ArgumentParser(description="공식 자료 저장과 팀 인용 승인 대조를 구분한다.")
    parser.add_argument("path", nargs="?", type=Path, default=Path("data/knowledge/sources.json"))
    parser.add_argument("--approval-manifest", type=Path, default=None)
    parser.add_argument(
        "--validate-only", action="store_true", help="DB에 연결하지 않고 인용 승인 증거만 대조"
    )
    args = parser.parse_args()
    if not args.path.is_file():
        raise SystemExit("공식 자료 JSON 파일을 찾을 수 없습니다.")
    local = LocalKnowledgeBase.from_json(args.path)
    manifest = args.approval_manifest or Path(
        os.environ.get("HJ_KNOWLEDGE_APPROVAL_PATH", str(args.path.with_name("approvals.json")))
    )
    approved = ApprovedKnowledgeBase.from_file(local, manifest)
    allowed, decisions = approved.evaluate([c.id for c in local.chunks])
    report = {
        "storage": "NOT RUN",
        "approved_chunks": len(allowed),
        "held_chunks": sum(bool(reasons) for reasons in decisions.values()),
        "decisions": decisions,
        "meaning": (
            "사유 목록이 빈 청크만 원문 대조와 팀 인용 승인 조건을 충족합니다. "
            "법적 적용이나 환불 결과 확정이 아닙니다."
        ),
    }
    if args.validate_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(0 if local.chunks and len(allowed) == len(local.chunks) else 2)
    url = os.environ.get("HJ_KNOWLEDGE_DATABASE_URL")
    if not url:
        raise SystemExit("환경변수 HJ_KNOWLEDGE_DATABASE_URL 이 필요합니다.")
    engine, _ = build_session_factory(url)
    try:
        n = load_knowledge(engine, local.sources, local.chunks)
    finally:
        engine.dispose()
    # 미승인 자료의 저장은 인용 승격이 아니다. 생산 컨테이너는 같은 승인 어댑터로 조회한다.
    report.update(storage="PASS", stored_chunks=n)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
