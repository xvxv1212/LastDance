"""RAG 전송 준비 전용. 모델 호출·DB 쓰기·승인 승격을 수행하지 않는다.

related_ids는 서버 관리 연결표이며 요청/LLM의 입력으로 받아서는 안 된다.
현재 모든 연결은 필수로 취급한다. 관계의 신뢰 검토 및 DB 적재는 별도 단계다.
"""
from __future__ import annotations

import json
import uuid
from collections import deque
from dataclasses import dataclass

from app.application.ports.services import ApprovedEvidenceReader
from app.domain.entities import ExtractedFact
from app.domain.enums import FactSourceKind, FactStatus
from app.domain.policies import PersonalInfoMasker


@dataclass(frozen=True)
class PacketPreparation:
    review_json: str
    blocked_reasons: tuple[str, ...]
    # 아직 모델 호출 계약/출력 검증이 연결되지 않았다. 호출 허가는 제공하지 않는다.
    generation_allowed: bool = False


class PrepareEvidencePacket:
    def __init__(
        self, reader: ApprovedEvidenceReader, related_ids: dict[str, tuple[str, ...]],
        *, max_depth: int = 2, max_records: int = 20,
        max_user_chars: int = 6000, max_evidence_chars: int = 6000, max_total_chars: int = 10000,
    ) -> None:
        if not isinstance(reader, ApprovedEvidenceReader):
            raise TypeError("승인 검증 포트가 필요합니다.")
        if max_depth < 0 or min(max_records, max_user_chars, max_evidence_chars, max_total_chars) < 1:
            raise ValueError("잘못된 예산 설정")
        self.reader = reader
        self.links = {key: tuple(value) for key, value in related_ids.items()}
        self.depth = max_depth
        self.count = max_records
        self.user_limit = max_user_chars
        self.evidence_limit = max_evidence_chars
        self.total_limit = max_total_chars

    def execute(
        self, *, session_id: uuid.UUID, user_id: uuid.UUID, text: str,
        facts: list[ExtractedFact], direct_ids: list[str], required_fact_keys: tuple[str, ...],
    ) -> PacketPreparation:
        """호출자는 먼저 점검 소유권을 검증해야 한다. 필수 사실 목록은 서버 규칙이다."""
        masker = PersonalInfoMasker()
        masked = masker.mask(text)
        reasons: list[str] = []
        confirmed = []
        known_keys = set()
        seen_keys = set()
        for fact in facts:
            if fact.check_session_id != session_id:
                reasons.append("FACT_SESSION_MISMATCH")
                continue
            if fact.confirmed_by != user_id or fact.confirmed_at is None:
                continue
            if fact.fact_key.value in seen_keys:
                reasons.append("DUPLICATE_FACT_KEY")
                continue
            seen_keys.add(fact.fact_key.value)
            if fact.has_conflict:
                reasons.append("FACT_CONFLICT")
                continue
            if fact.source_kind == FactSourceKind.UNKNOWN:
                if fact.status == FactStatus.ACKNOWLEDGED_UNKNOWN:
                    confirmed.append({"key": fact.fact_key.value, "state": "UNKNOWN", "value": None})
                continue
            if fact.is_confirmed and fact.value_text and fact.value_text.strip():
                known_keys.add(fact.fact_key.value)
                confirmed.append({"key": fact.fact_key.value, "state": "CONFIRMED",
                                  "value": masker.mask(fact.value_text)})
        if not known_keys:
            reasons.append("CASE_FACTS_NOT_CONFIRMED")
        for key in required_fact_keys:
            if key not in known_keys:
                reasons.append("MISSING_REQUIRED_FACT:" + key)
        if not required_fact_keys:
            reasons.append("SUFFICIENCY_RULES_NOT_CONFIGURED")

        seeds = list(dict.fromkeys(direct_ids))
        if len(seeds) > self.count:
            reasons.append("EVIDENCE_COUNT_EXCEEDED")
        queue = deque((cid, None, 0) for cid in seeds[:self.count])
        visited = set(seeds[:self.count])
        evidence = []
        while queue:
            cid, parent, depth = queue.popleft()
            allowed, decisions = self.reader.evaluate([cid])
            chunk = allowed.get(cid)
            if chunk is None or decisions.get(cid) != []:
                codes = decisions.get(cid) or ["APPROVAL_DECISION_MISSING"]
                reasons.extend(f"EVIDENCE_BLOCKED:{cid}:{code}" for code in codes)
                continue
            if not chunk.source_active:
                reasons.append("SOURCE_INACTIVE:" + cid)
                continue
            # 본문을 자르지 않는다. 민감정보가 감지된 자료는 전송 후보에서 제외한다.
            if masker.mask(chunk.content) != chunk.content:
                reasons.append("EVIDENCE_SENSITIVE_CONTENT:" + cid)
                continue
            evidence.append({"id": cid, "role": "DIRECT" if parent is None else "LINKED",
                             "included_via": parent, "title": chunk.source_title,
                             "content": chunk.content, "source_id": chunk.source_id,
                             "version": chunk.source_version, "locator": chunk.article_ref,
                             "effective_date": (
                                 chunk.effective_date.isoformat() if chunk.effective_date else None
                             ),
                             "approval": "RECEIPT_VERIFIED", "applicability": "UNREVIEWED"})
            for child in self.links.get(cid, ()):
                if child in visited:
                    continue
                if depth >= self.depth:
                    reasons.append("LINK_DEPTH_EXCEEDED:" + child)
                elif len(visited) >= self.count:
                    reasons.append("EVIDENCE_COUNT_EXCEEDED")
                else:
                    visited.add(child)
                    queue.append((child, cid, depth + 1))
        if not evidence:
            reasons.append("NO_APPROVED_EVIDENCE")
        # 시행일만으로 사건 적용을 확정하지 않는다. 시점·의미 검증까지 보류한다.
        reasons.extend(("APPLICABILITY_NOT_REVIEWED", "SEMANTIC_SUFFICIENCY_NOT_REVIEWED"))
        evidence_json = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        payload = json.dumps({"purpose": "RAG_REVIEW_ONLY", "user_text": masked,
                              "facts": confirmed, "evidence": evidence},
                             ensure_ascii=False, separators=(",", ":"))
        if len(masked) > self.user_limit:
            reasons.append("USER_INPUT_TOO_LARGE")
        if len(evidence_json) > self.evidence_limit:
            reasons.append("EVIDENCE_INPUT_TOO_LARGE")
        if len(payload) > self.total_limit:
            reasons.append("TOTAL_INPUT_TOO_LARGE")
        return PacketPreparation(payload, tuple(dict.fromkeys(reasons)))
