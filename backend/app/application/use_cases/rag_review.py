
from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.application.use_cases.check_session import ListSessionFacts
from app.application.use_cases.evidence_packet import PacketPreparation, PrepareEvidencePacket
from app.domain.enums import FactStatus, SessionStatus
from app.domain.errors import ConflictError
from app.domain.policies import PersonalInfoMasker


@dataclass(frozen=True)
class RagHit:
    chunk_id: str
    distance: float  # 확률이나 법률적 신뢰도가 아닌 검색 거리


@dataclass(frozen=True)
class RagReview:
    facts_confirmed_at: datetime
    search_status: str
    candidate_ids: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    packet: PacketPreparation | None = None
    generation_allowed: bool = False


class PrepareSessionRagReview:
    """S07 확인 사실 -> 검색 후보 -> 승인 검토용 근거 패킷."""

    def __init__(
        self,
        facts_reader: ListSessionFacts,
        packet_builder: PrepareEvidencePacket,
        retriever: Callable[[str], Sequence[RagHit]] | None = None,
    ) -> None:
        self.facts_reader = facts_reader
        self.packet_builder = packet_builder
        self.retriever = retriever

    def execute(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        required_fact_keys: tuple[str, ...] = (),
    ) -> RagReview:
        data = self.facts_reader.execute(user_id, session_id)
        data.session.ensure_owned_by(user_id)
        stamp = data.session.facts_confirmed_at

        if data.session.status != SessionStatus.FACTS_CONFIRMED or stamp is None:
            raise ConflictError("사실 확인 완료 후에만 검색할 수 있습니다.")

        handled = {
            FactStatus.CONFIRMED,
            FactStatus.MODIFIED,
            FactStatus.REJECTED,
            FactStatus.ACKNOWLEDGED_UNKNOWN,
        }
        if any(
            f.check_session_id != session_id or f.status not in handled
            for f in data.facts
        ):
            raise ConflictError("미처리·알 수 없는 상태 또는 다른 점검의 사실이 있습니다.")

        known = [f for f in data.facts if f.is_confirmed]
        if not known or any(
            f.confirmed_by != user_id or f.confirmed_at is None
            for f in known
        ):
            raise ConflictError("사용자가 확인한 사실이 필요합니다.")

        parts = [data.session.situation_text or ""]
        parts.extend(
            f"{f.fact_key.value}: {f.value_text}"
            for f in known if f.value_text
        )
        query = PersonalInfoMasker().mask(" | ".join(parts)).strip()

        if not query or len(query) > 6000:
            return RagReview(
                stamp, "BLOCKED",
                blocked_reasons=("QUERY_INVALID_OR_TOO_LONG",),
            )

        if self.retriever is None:
            return RagReview(
                stamp, "NOT_CONFIGURED",
                blocked_reasons=("SEARCH_NOT_CONFIGURED",),
            )

        try:
            hits = tuple(self.retriever(query))
        except Exception:
            return RagReview(
                stamp, "BLOCKED",
                blocked_reasons=("SEARCH_FAILED",),
            )

        if len(hits) > 5 or any(
            not isinstance(h, RagHit)
            or not isinstance(h.chunk_id, str)
            or not h.chunk_id
            or not isinstance(h.distance, (float, int))
            or not math.isfinite(h.distance)
            for h in hits
        ):
            return RagReview(
                stamp, "BLOCKED",
                blocked_reasons=("SEARCH_RESULTS_INVALID",),
            )

        ids = list(dict.fromkeys(h.chunk_id for h in hits))
        packet = self.packet_builder.execute(
            session_id=session_id,
            user_id=user_id,
            text=data.session.situation_text or "",
            facts=data.facts,
            direct_ids=ids,
            required_fact_keys=required_fact_keys,
        )

        reasons = (
            (("NO_SEARCH_RESULTS",) if not ids else ())
            + packet.blocked_reasons
        )
        return RagReview(
            stamp,
            "REVIEW_ONLY" if ids else "NO_HITS",
            tuple(ids),
            tuple(dict.fromkeys(reasons)),
            packet,
        )
