"""공식 근거의 팀 인용 승인 대조(서버 관리 manifest).

서버의 VERIFIED는 본문 일치 등을 뜻할 뿐 팀 인용 승인이 아니다. 이 어댑터는
본문 SHA-256·출처 ID/URL/버전·원문 발췌·사용자 승인 기록이 모두 맞는 청크만
기존 CitationVerifier에 전달한다. 미승인 자료는 기존 인용 실패/보류 응답을 따른다.
manifest와 증거는 서버 배포자가 관리하며 시민 요청이나 모델 출력에서 받지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.application.ports.services import KnowledgeBase
from app.domain.policies import ChunkFacts


def content_sha256(text: str) -> str:
    """변경을 숨기는 공백 정규화 없이 실제 UTF-8 본문을 대조한다."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ApprovedKnowledgeBase(KnowledgeBase):
    """본문 저장소의 조회 결과를 신뢰한 승인 증거와 대조한다. DB 스키마 변경 없음."""

    def __init__(self, delegate: KnowledgeBase, manifest: dict[str, Any], evidence_root: Path) -> None:
        self._delegate = delegate
        self._manifest = manifest
        self._root = evidence_root.resolve()
        if manifest and manifest.get("schema_version") != 1:
            raise ValueError("지원하지 않는 인용 승인 기록 형식입니다.")
        if not isinstance(manifest.get("chunks", {}), dict):
            raise ValueError("인용 승인 기록의 chunks는 객체여야 합니다.")

    @classmethod
    def from_file(cls, delegate: KnowledgeBase, path: Path) -> ApprovedKnowledgeBase:
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(manifest, dict):
            raise ValueError("인용 승인 기록은 JSON 객체여야 합니다.")
        return cls(delegate, manifest, path.resolve().parent)

    def _evidence(self, relative: Any, expected_sha: Any) -> bytes | None:
        if not isinstance(relative, str) or not relative or not isinstance(expected_sha, str):
            return None
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root) or not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return data if hashlib.sha256(data).hexdigest() == expected_sha else None

    def _reasons(self, chunk_id: str, chunk: ChunkFacts | None) -> list[str]:
        if chunk is None:
            return ["BASE_CHUNK_MISSING"]
        record = self._manifest.get("chunks", {}).get(chunk_id)
        if not isinstance(record, dict):
            return ["TEAM_APPROVAL_MISSING"]
        reasons = []
        actual_sha = content_sha256(chunk.content)
        for actual, expected, code in (
            (chunk_id, record.get("chunk_id"), "CHUNK_ID_MISMATCH"),
            (chunk.source_id, record.get("source_id"), "SOURCE_ID_MISMATCH"),
            (chunk.source_url, record.get("source_url"), "SOURCE_URL_MISMATCH"),
            (chunk.source_version, record.get("source_version"), "SOURCE_VERSION_MISMATCH"),
            (actual_sha, record.get("content_sha256"), "CONTENT_HASH_MISMATCH"),
        ):
            if not actual or actual != expected:
                reasons.append(code)
        original = record.get("original_comparison") or {}
        if not isinstance(original, dict):
            return [*reasons, "ORIGINAL_EVIDENCE_INVALID"]
        original_bytes = self._evidence(original.get("excerpt_file"), original.get("excerpt_sha256"))
        if (
            original.get("verified") is not True
            or original.get("scope") != "CHUNK_WITH_PARENT_CONTEXT"
            or not original.get("locator")
            or not original.get("checked_on")
            or original.get("source_url") != chunk.source_url
            or original.get("source_version") != chunk.source_version
            or original_bytes is None
        ):
            reasons.append("ORIGINAL_EVIDENCE_INVALID")
        else:
            try:
                original_text = original_bytes.decode("utf-8")
            except UnicodeDecodeError:
                original_text = ""
            if not chunk.content or chunk.content not in original_text:
                reasons.append("CONTENT_NOT_IN_ORIGINAL_EXCERPT")
        approval = record.get("team_approval") or {}
        if not isinstance(approval, dict):
            return [*reasons, "TEAM_APPROVAL_INVALID"]
        receipt_bytes = self._evidence(approval.get("record_file"), approval.get("record_sha256"))
        if (
            approval.get("approved") is not True
            or not approval.get("approved_by")
            or not approval.get("approved_on")
            or approval.get("source_url") != chunk.source_url
            or approval.get("source_version") != chunk.source_version
            or approval.get("content_sha256") != actual_sha
            or receipt_bytes is None
        ):
            reasons.append("TEAM_APPROVAL_INVALID")
        else:
            try:
                receipt = json.loads(receipt_bytes)
            except (ValueError, UnicodeDecodeError):
                receipt = {}
            binding = {
                "chunk_id": chunk_id,
                "source_id": chunk.source_id,
                "source_url": chunk.source_url,
                "source_version": chunk.source_version,
                "content_sha256": actual_sha,
            }
            if (
                not isinstance(receipt, dict)
                or receipt.get("approved_by") != approval["approved_by"]
                or receipt.get("approved_on") != approval["approved_on"]
                or receipt.get("binding") != binding
                or receipt.get("authority") != "USER_APPROVAL"
                or not receipt.get("user_instruction")
            ):
                reasons.append("APPROVAL_RECEIPT_MISMATCH")
        return reasons

    def evaluate(self, chunk_ids: list[str]) -> tuple[dict[str, ChunkFacts], dict[str, list[str]]]:
        """검증 명령용 근거 판정. reasons=[]인 청크만 인용 검증에 전달할 수 있다."""
        candidates = self._delegate.chunk_facts(chunk_ids)
        decisions = {cid: self._reasons(cid, candidates.get(cid)) for cid in chunk_ids}
        return {cid: candidates[cid] for cid, reasons in decisions.items() if not reasons}, decisions

    def chunk_facts(self, chunk_ids: list[str]) -> dict[str, ChunkFacts]:
        allowed, _ = self.evaluate(chunk_ids)
        return allowed
