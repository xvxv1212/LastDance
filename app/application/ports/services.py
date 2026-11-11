"""저장소 외의 외부 기능 포트(시간, 파일 저장, 지식 DB, 기관 목록, 작업 단위)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType

from app.application.ports.repositories import (
    AuditLogRepository,
    CaseRecordRepository,
    CaseRepository,
    CheckSessionRepository,
    ConsentRepository,
    DocumentRepository,
    FactRepository,
    PurgeRepository,
    ReportRepository,
    UserRepository,
)
from app.domain.entities import SupportAgency
from app.domain.policies import ChunkFacts


class Clock(ABC):
    """현재 시각 제공. 테스트에서 시간을 고정할 수 있도록 포트로 분리한다."""

    @abstractmethod
    def now(self) -> datetime: ...


class FileStorage(ABC):
    """파일 본문 저장소(로컬 디스크·S3·NCP Object Storage 등으로 교체 가능)."""

    @abstractmethod
    def put(self, content: bytes) -> str:
        """파일을 저장하고, 추측 불가능한 저장 키를 돌려준다."""

    @abstractmethod
    def delete(self, storage_key: str) -> None: ...


@dataclass(frozen=True)
class ModelResult:
    text: str
    model_name: str
    input_tokens: int
    output_tokens: int


class ModelNotConfiguredError(Exception):
    """외부 모델이 설정되지 않아 '보내기 전에' 거절됐다는 뜻. 이 경우 외부 전송은 일어나지 않았다(v0.8)."""


class ExternalModelClient(ABC):
    """외부(해외 포함) LLM·분류 API. InputGuard 를 통과한 마스킹 텍스트만 받는다(AI-02).

    네트워크로 보내기 전에 거절하는 구현은 ModelNotConfiguredError 를 던져야 한다
    → 감사 로그에 '외부 전송 없음'으로 정확히 남는다.
    """

    @abstractmethod
    def complete(self, node_name: str, masked_text: str) -> ModelResult: ...


class CheckpointStore(ABC):
    """LangGraph 체크포인트 저장소(라이브러리 관리 테이블). 업무 DB 와 분리돼 있어 파기를 따로 호출한다."""

    @abstractmethod
    def delete_thread(self, thread_id: str) -> None: ...


class KnowledgeBase(ABC):
    """공식 지식 DB(벡터 DB) 조회 포트 — 업무 DB 트랜잭션(UnitOfWork)과 분리돼 있다.

    v0.6(DB 멘토링 피드백): 공식 출처·청크·임베딩은 업무 DB 가 아니라 벡터 지식 DB 에 둔다.
    - 업무 DB 와 다른 저장소이므로 FK 로 연결하지 않는다 → 보고서에는 청크 ID '문자열'과
      인용 당시 출처 정보 사본을 남긴다.
    - 구현체 교체(로컬 JSON → Chroma·Qdrant·pgvector 별도 스키마)는 이 포트 뒤에서만 일어난다(DIP).
    - 유사도 검색(RAG retrieval)은 AI 파트(LangChain) 담당이고, 백엔드는 '인용 검증용 조회'만 쓴다.
    """

    @abstractmethod
    def chunk_facts(self, chunk_ids: list[str]) -> dict[str, ChunkFacts]:
        """인용 검증용 청크 정보(본문·출처 활성 여부·시행일·출처 메타데이터). 없는 ID 는 결과에 없다."""


class PasswordHasher(ABC):
    """비밀번호 단방향 해시(v0.9). 구현은 Argon2id(안전성 확보조치 기준 제7조: 일방향 암호화)."""

    @abstractmethod
    def hash(self, password: str) -> str: ...

    @abstractmethod
    def verify(self, password_hash: str, password: str) -> bool:
        """일치하면 True. 해시 형식이 틀려도 예외 대신 False."""

    @abstractmethod
    def dummy_verify(self, password: str) -> None:
        """없는 이메일로 로그인할 때도 같은 시간이 걸리게 한다(가입 여부 추측 방지)."""


class AgencyDirectory(ABC):
    """지원기관 목록 조회 포트. v0.6: DB 테이블 대신 설정 파일(data/agencies.json)."""

    @abstractmethod
    def get(self, code: str) -> SupportAgency | None: ...


class UnitOfWork(ABC):
    """작업 단위(Unit of Work).

    하나의 유스케이스에서 일어난 여러 저장소 변경을 '하나의 트랜잭션'으로 묶는다.
    `with uow:` 블록 안에서 commit() 하지 않으면 자동으로 rollback 된다.
    """

    users: UserRepository
    consents: ConsentRepository
    sessions: CheckSessionRepository
    documents: DocumentRepository
    facts: FactRepository
    reports: ReportRepository
    purge: PurgeRepository
    cases: CaseRepository
    case_records: CaseRecordRepository
    audit_logs: AuditLogRepository

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # 커밋되지 않은 변경은 항상 취소 → 예외 시 부분 저장이 남지 않는다.
        self.rollback()

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...
