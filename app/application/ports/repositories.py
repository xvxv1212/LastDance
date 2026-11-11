"""저장소(Repository) 포트.

인터페이스 분리 원칙(ISP)에 따라 '거대한 하나의 DAO' 대신
애그리거트(업무 묶음)별로 작은 인터페이스를 나눴다.
각 유스케이스는 자신이 실제로 쓰는 저장소만 의존한다.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain.entities import (
    AuditLog,
    Case,
    CaseEvent,
    CheckSession,
    Document,
    Evidence,
    ExtractedFact,
    InspectionReport,
    User,
    UserConsent,
)
from app.domain.enums import ConsentType, PurgeTaskKind


class UserRepository(ABC):
    """회원 계정(v0.9). 가입·로그인 조회는 RLS 밖(인증 전용 접속)에서, 그 밖은 본인 행만 다룬다."""

    @abstractmethod
    def get(self, user_id: uuid.UUID) -> User | None: ...

    @abstractmethod
    def find_by_email(self, email: str) -> User | None:
        """정규화(소문자)된 이메일로 찾는다. 탈퇴 행은 이메일이 NULL 이라 찾히지 않는다."""

    @abstractmethod
    def add(self, user: User) -> None: ...

    @abstractmethod
    def save(self, user: User) -> None: ...


class ConsentRepository(ABC):
    @abstractmethod
    def add(self, consent: UserConsent) -> None: ...

    @abstractmethod
    def latest(self, user_id: uuid.UUID, consent_type: ConsentType) -> UserConsent | None:
        """해당 종류의 가장 최근 동의 이력(없으면 None)."""


class CheckSessionRepository(ABC):
    @abstractmethod
    def add(self, session: CheckSession) -> None: ...

    @abstractmethod
    def get(self, session_id: uuid.UUID) -> CheckSession | None: ...

    @abstractmethod
    def save(self, session: CheckSession) -> None: ...

    @abstractmethod
    def find_by_thread(self, thread_id: str) -> CheckSession | None:
        """thread_id 로 점검을 찾는다(thread_id UNIQUE → 0~1건)."""

    @abstractmethod
    def soft_delete_by_owner(self, user_id: uuid.UUID, now: datetime) -> int:
        """회원 탈퇴(v0.9): 그 사용자의 모든 점검을 논리 삭제(이후 파기 배치가 물리 삭제)."""


class DocumentRepository(ABC):
    @abstractmethod
    def add(self, document: Document) -> None: ...

    @abstractmethod
    def get(self, document_id: uuid.UUID) -> Document | None: ...

    @abstractmethod
    def list_by_session(self, session_id: uuid.UUID) -> list[Document]: ...

    @abstractmethod
    def save(self, document: Document) -> None: ...


class FactRepository(ABC):
    @abstractmethod
    def add_many(self, facts: list[ExtractedFact]) -> None: ...

    @abstractmethod
    def get(self, fact_id: uuid.UUID) -> ExtractedFact | None: ...

    @abstractmethod
    def list_by_session(self, session_id: uuid.UUID) -> list[ExtractedFact]: ...

    @abstractmethod
    def save(self, fact: ExtractedFact) -> None: ...


class ReportRepository(ABC):
    @abstractmethod
    def add(self, report: InspectionReport) -> None:
        """보고서와 하위 항목·인용을 한 번에 저장한다(애그리거트 단위 저장)."""

    @abstractmethod
    def get(self, report_id: uuid.UUID) -> InspectionReport | None: ...

    @abstractmethod
    def save_status(self, report: InspectionReport) -> None:
        """보고서 상태·승인시각만 갱신한다(본문은 승인 후 불변)."""

    @abstractmethod
    def latest_approved(self, session_id: uuid.UUID) -> InspectionReport | None: ...

    @abstractmethod
    def latest_version(self, session_id: uuid.UUID) -> int:
        """세션의 최신 보고서 버전(없으면 0)."""

    @abstractmethod
    def supersede_previous(self, session_id: uuid.UUID) -> None:
        """새 보고서 승인 시 이전 승인본을 '대체됨'으로 바꾼다."""


class CaseRepository(ABC):
    @abstractmethod
    def add(self, case: Case) -> None: ...

    @abstractmethod
    def get(self, case_id: uuid.UUID) -> Case | None: ...

    @abstractmethod
    def save(self, case: Case) -> None: ...

    @abstractmethod
    def list_by_owner(self, owner_id: uuid.UUID) -> list[Case]: ...

    @abstractmethod
    def find_by_origin_session(self, session_id: uuid.UUID) -> Case | None:
        """점검에서 전환된 사건(origin_session_id UNIQUE 이므로 0~1건)."""

    @abstractmethod
    def find_by_thread(self, thread_id: str) -> Case | None:
        """thread_id 로 사건을 찾는다(thread_id UNIQUE → 0~1건)."""

    @abstractmethod
    def soft_delete_by_owner(self, owner_id: uuid.UUID, now: datetime) -> int:
        """회원 탈퇴(v0.9): 그 사용자의 모든 사건을 논리 삭제."""


class CaseRecordRepository(ABC):
    """사건에 딸린 '하나씩 추가·수정·연결되는' 기록(타임라인·증거) 저장소.

    v0.6: 스냅샷·미확인 정보·상담 준비자료는 cases 의 컬럼이 되어 CaseRepository 로 함께 저장된다.
    """

    @abstractmethod
    def add_event(self, event: CaseEvent) -> None: ...

    @abstractmethod
    def list_events(self, case_id: uuid.UUID) -> list[CaseEvent]: ...

    @abstractmethod
    def add_evidence(self, evidence: Evidence) -> None: ...

    @abstractmethod
    def list_evidences(self, case_id: uuid.UUID) -> list[Evidence]: ...


@dataclass(frozen=True)
class PurgeTask:
    """아직 끝나지 않은 DB 밖 삭제 작업 1건(v0.11). 행이 남아 있다 = 아직 지우지 못했다."""

    id: uuid.UUID
    kind: PurgeTaskKind
    target: str
    attempts: int


class PurgeRepository(ABC):
    """보관기한·삭제 요청에 따른 물리 파기 전용 저장소(DB-04). 일반 유스케이스에는 노출하지 않는다(ISP)."""

    @abstractmethod
    def cases_deleted_before(self, cutoff: datetime) -> list[uuid.UUID]: ...

    @abstractmethod
    def documents_of_case(self, case_id: uuid.UUID) -> list[Document]: ...

    @abstractmethod
    def thread_ids_of(self, case_id: uuid.UUID | None, session_id: uuid.UUID | None) -> list[str]:
        """사건·원본 점검의 thread_id(컬럼 값). 파기 후 LangGraph 체크포인트 삭제에 쓴다."""

    @abstractmethod
    def hard_delete_case(self, case_id: uuid.UUID) -> None:
        """사건과 하위 기록(CASCADE)을 물리 삭제. 원본 점검도 함께 삭제한다."""

    # --- v0.9 데이터별 보관 정책 -------------------------------------------
    @abstractmethod
    def idle_unconverted_sessions(self, cutoff: datetime) -> list[uuid.UUID]:
        """사건으로 전환되지 않았고 마지막 이용(점검·문서·사실·보고서 중 가장 늦은 시각)이 cutoff 전."""

    @abstractmethod
    def cases_closed_before(self, cutoff: datetime) -> list[uuid.UUID]:
        """종결(CLOSED)되고 종결 시각이 cutoff 전인 사건."""

    @abstractmethod
    def documents_of_session(self, session_id: uuid.UUID) -> list[Document]: ...

    @abstractmethod
    def hard_delete_session(self, session_id: uuid.UUID) -> None:
        """점검과 하위 기록(문서·사실·보고서, CASCADE)을 물리 삭제."""

    @abstractmethod
    def withdrawn_users_with_data(self) -> list[uuid.UUID]:
        """탈퇴했지만 점검·사건·동의 이력이 아직 남은 사용자(탈퇴 직후 파기가 실패한 경우 포함)."""

    @abstractmethod
    def case_ids_of_user(self, user_id: uuid.UUID) -> list[uuid.UUID]: ...

    @abstractmethod
    def session_ids_of_user(self, user_id: uuid.UUID) -> list[uuid.UUID]: ...

    @abstractmethod
    def delete_consents_of_user(self, user_id: uuid.UUID) -> int: ...

    @abstractmethod
    def delete_audit_logs_before(self, cutoff: datetime) -> int: ...

    @abstractmethod
    def delete_withdrawn_users_before(self, cutoff: datetime) -> int:
        """탈퇴 후 cutoff 가 지났고 감사 로그가 더 이상 가리키지 않는 빈 계정 행을 지운다."""

    # --- v0.11 파기 대기 작업(DB 밖 자료 삭제의 영속 기록) -----------------------
    @abstractmethod
    def enqueue_tasks(self, kind: PurgeTaskKind, targets: list[str], now: datetime) -> int:
        """삭제 대상을 기록한다. DB 행을 지우는 '같은 트랜잭션'에서 불러야 한다. 이미 있으면 건너뛴다."""

    @abstractmethod
    def pending_tasks(self, kind: PurgeTaskKind) -> list[PurgeTask]: ...

    @abstractmethod
    def complete_task(self, task_id: uuid.UUID) -> None:
        """삭제가 끝난 작업의 기록을 지운다(대상 식별자를 더 보관하지 않는다)."""

    @abstractmethod
    def fail_task(self, task_id: uuid.UUID, code: str, now: datetime) -> None:
        """실패를 기록한다(시도 횟수 +1, 사유 코드, 시각). 다음 배치가 다시 시도한다."""


class AuditLogRepository(ABC):
    @abstractmethod
    def add(self, log: AuditLog) -> None: ...
