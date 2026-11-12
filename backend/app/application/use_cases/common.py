"""유스케이스 공통 도우미."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from app.application.ports.services import Clock, FileStorage, UnitOfWork
from app.domain.entities import AuditLog, Document, new_id
from app.domain.enums import AuditAction, ConsentType
from app.domain.errors import ConsentRequiredError
from app.domain.policies import UploadPolicy


def require_consent(uow: UnitOfWork, user_id: uuid.UUID, consent_type: ConsentType) -> None:
    """필요한 동의가 '현재 유효'한지 확인한다. 철회했다면 다시 동의해야 한다."""
    latest = uow.consents.latest(user_id, consent_type)
    if latest is None or not latest.is_effective:
        raise ConsentRequiredError(f"'{consent_type}' 동의가 필요합니다.")


def audit(
    uow: UnitOfWork,
    clock: Clock,
    actor_id: uuid.UUID | None,
    action: AuditAction,
    target_type: str,
    target_id: uuid.UUID | None,
    detail: dict[str, Any] | None = None,
) -> uuid.UUID:
    """감사 로그 1건을 같은 트랜잭션에 기록하고 로그 ID 를 돌려준다(업무 변경과 로그가 함께 커밋/롤백)."""
    log = AuditLog(
        id=new_id(),
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        created_at=clock.now(),
    )
    uow.audit_logs.add(log)
    return log.id


def store_document(
    uow: UnitOfWork,
    clock: Clock,
    storage: FileStorage,
    *,
    owner_id: uuid.UUID,
    filename: str,
    mime_type: str,
    content: bytes,
    retention_days: int,
    check_session_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """검증을 마친 파일을 저장소에 넣고 메타데이터·감사 로그를 기록한 뒤 커밋한다.

    점검 업로드(UC-03)와 사건 추가 첨부(UC-18)가 같이 쓴다. 보상 처리를 한곳에 두어
    한쪽만 고쳐지는 일을 막기 위함이다. 열린 UoW(`with uow`) 안에서 호출해야 한다.
    부모는 점검 또는 사건 중 하나다(DB 제약 ck_documents_parent_required 와 같은 규칙).
    DB 기록이 실패하면 이미 저장한 파일을 지워 '고아 파일'이 남지 않게 한다.
    """
    now = clock.now()
    storage_key = storage.put(content)
    try:
        doc = Document(
            id=new_id(),
            owner_id=owner_id,
            check_session_id=check_session_id,
            case_id=case_id,
            original_name=UploadPolicy.safe_display_name(filename),
            mime_type=mime_type,
            size_bytes=len(content),
            storage_key=storage_key,
            sha256=hashlib.sha256(content).hexdigest(),  # 위·변조 확인용
            created_at=now,
            retention_until=(now + timedelta(days=retention_days)).date(),
        )
        uow.documents.add(doc)
        audit(uow, clock, owner_id, AuditAction.CREATE, "documents", doc.id, {"size": doc.size_bytes})
        uow.commit()
    except Exception:
        storage.delete(storage_key)  # 보상 처리
        raise
    return doc.id
