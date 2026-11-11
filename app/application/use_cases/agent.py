"""AI 실행 스레드·외부 모델 호출·보관기한 파기 유스케이스.

UC-15 AI 실행 스레드 시작(DB-05) / UC-16 외부 모델 호출 전 InputGuard(AI-02) /
UC-17 보관기한 경과·삭제 사건 파기 배치(DB-04)

v0.6(DB 멘토링 피드백): agent_threads·agent_runs 테이블을 없앴다.
- thread_id 는 check_sessions.thread_id / cases.thread_id 컬럼(UNIQUE)에 둔다.
  → '스레드 = 사용자 + (점검 또는 사건) 하나' 규칙은 부모 행 자체가 보장한다.
- 외부 전송·차단 사실은 audit_logs(AI_CALL)에, 토큰·지연시간은 애플리케이션 로그에 남긴다.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.application.ports.services import (
    CheckpointStore,
    Clock,
    ExternalModelClient,
    FileStorage,
    ModelNotConfiguredError,
    UnitOfWork,
)
from app.application.use_cases.common import audit
from app.domain.entities import AiCallRecord, Case, CheckSession
from app.domain.enums import AgentRunStatus, AuditAction, ConsentType, GuardCode, TransferBasis, UserStatus
from app.domain.errors import NotFoundError, ValidationError
from app.domain.policies import ExternalTransferPolicy, PersonalInfoMasker

# 성능·비용 지표 전용 로거(원문 금지: 노드·상태·모델·토큰·지연시간만)
ai_metrics = logging.getLogger("gwonri.ai.metrics")


# ===========================================================================
# UC-15 AI 실행 스레드 시작
# ===========================================================================
@dataclass(frozen=True)
class StartThreadCommand:
    user_id: uuid.UUID
    check_session_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None


class StartAgentThread:
    """thread_id 를 '사용자 + (점검 또는 사건) 하나'에 묶어 발급한다.

    - 점검·사건 행의 thread_id 컬럼에 저장하므로 소유자·부모가 따로 기록될 필요가 없다.
    - 이미 발급된 스레드가 있으면 그대로 돌려준다(멱등: 재시도·새로고침에도 스레드 1개).
    - thread_id 는 추측 불가 랜덤 값이며, 다른 사용자가 알더라도 소유자 검사에서 막힌다.
    """

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def execute(self, cmd: StartThreadCommand) -> str:
        if (cmd.check_session_id is None) == (cmd.case_id is None):
            raise ValidationError("점검 또는 사건 중 정확히 하나를 지정해 주세요.")
        now = self._clock.now()
        with self._uow as uow:
            if cmd.check_session_id is not None:
                session = uow.sessions.get(cmd.check_session_id)
                if session is None:
                    raise NotFoundError("점검 기록을 찾을 수 없습니다.")
                session.ensure_owned_by(cmd.user_id)
                if session.thread_id is None:
                    session.thread_id = secrets.token_urlsafe(32)
                    session.updated_at = now
                    uow.sessions.save(session)
                thread_id = session.thread_id
            elif cmd.case_id is not None:
                case = uow.cases.get(cmd.case_id)
                if case is None:
                    raise NotFoundError("사건을 찾을 수 없습니다.")
                case.ensure_owned_by(cmd.user_id)
                if case.thread_id is None:
                    case.thread_id = secrets.token_urlsafe(32)
                    case.updated_at = now
                    uow.cases.save(case)
                thread_id = case.thread_id
            else:  # 위 검사로 도달 불가. 타입 검사기용
                raise ValidationError("점검 또는 사건 중 정확히 하나를 지정해 주세요.")
            uow.commit()
        return thread_id


def _resolve_thread(uow: UnitOfWork, thread_id: str, user_id: uuid.UUID) -> tuple[str, uuid.UUID]:
    """thread_id 의 부모(점검 또는 사건)를 찾고 소유자를 확인한다. (감사 대상 테이블, 부모 ID)

    타인·삭제된 부모의 스레드는 '없음'으로 처리해 재사용을 막는다(AI-04).
    """
    parent: CheckSession | Case | None = uow.sessions.find_by_thread(thread_id)
    target = "check_sessions"
    if parent is None:
        parent = uow.cases.find_by_thread(thread_id)
        target = "cases"
    if parent is None:
        raise NotFoundError("실행 스레드를 찾을 수 없습니다.")
    try:
        parent.ensure_owned_by(user_id)
    except NotFoundError:
        raise NotFoundError("실행 스레드를 찾을 수 없습니다.") from None
    return target, parent.id


# ===========================================================================
# UC-16 외부 모델 호출(InputGuard → 호출 → 기록), fail-closed
# ===========================================================================
@dataclass(frozen=True)
class ModelCallCommand:
    user_id: uuid.UUID
    thread_id: str
    node_name: str
    text: str


@dataclass(frozen=True)
class ModelCallOutcome:
    allowed: bool
    audit_id: uuid.UUID  # audit_logs(AI_CALL) 행 ID
    blocked_code: GuardCode | None = None
    output_text: str | None = None


class InvokeExternalModel:
    """외부 모델 API 를 부르기 '전에' 전송 가능 여부를 검사한다(AI-02).

    순서: ① thread 소유 확인 → ② 마스킹(최소화) → ③ 처리 근거·철회·민감정보·길이 판정
          → 통과 시에만 ④ 외부 호출 → ⑤ 기록(감사: 노드·상태·전송 여부·근거 코드 / 로그: 토큰·지연)
    차단되면 외부 호출은 0회이고, audit_logs 에 AI_CALL(BLOCKED + 사유 코드)만 남는다.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        client: ExternalModelClient,
        masker: PersonalInfoMasker,
        policy: ExternalTransferPolicy,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._client = client
        self._masker = masker
        self._policy = policy

    def execute(self, cmd: ModelCallCommand) -> ModelCallOutcome:
        with self._uow as uow:
            target, parent_id = _resolve_thread(uow, cmd.thread_id, cmd.user_id)
            latest = uow.consents.latest(cmd.user_id, ConsentType.OVERSEAS_TRANSFER)
            masked = self._masker.mask(cmd.text)
            decision = self._policy.decide(
                consent_effective=latest is not None and latest.is_effective,
                consent_withdrawn=latest is not None and not latest.is_effective,
                masked_text=masked,
            )
            if not decision.allowed:
                code = decision.code or GuardCode.NO_TRANSFER_BASIS  # 사유가 비면 가장 보수적인 코드
                record = AiCallRecord.blocked(cmd.node_name, code)
                audit_id = audit(
                    uow, self._clock, cmd.user_id, AuditAction.AI_CALL, target, parent_id, record.to_detail()
                )
                uow.commit()
                ai_metrics.info("ai_call", extra={"node": cmd.node_name, "status": "BLOCKED", "code": code})
                return ModelCallOutcome(False, audit_id, code)

        # DB 트랜잭션 밖에서 외부 호출(긴 네트워크 대기가 DB 잠금을 잡지 않게)
        started = time.monotonic()
        model_name: str | None = None
        tokens: tuple[int, int] | None = None
        sent = True  # v0.8: 보내기 전에 거절된 경우(모델 미설정)는 '외부 전송 없음'으로 기록
        try:
            result = self._client.complete(cmd.node_name, masked)
            status, error_code = AgentRunStatus.SUCCEEDED, None
            model_name, tokens = result.model_name, (result.input_tokens, result.output_tokens)
            output: str | None = result.text
        except ModelNotConfiguredError:
            status, error_code, output, sent = AgentRunStatus.FAILED, "MODEL_NOT_CONFIGURED", None, False
        except Exception:  # 외부 오류 내용(원문 포함 가능)은 기록하지 않는다
            status, error_code, output = AgentRunStatus.FAILED, "MODEL_ERROR", None
        latency_ms = int((time.monotonic() - started) * 1000)
        record = AiCallRecord(
            cmd.node_name,
            status,
            external_api=sent,
            transfer_basis=decision.basis if sent else TransferBasis.NOT_APPLICABLE,
            error_code=error_code,
            model_name=model_name,
        )
        with self._uow as uow:
            audit_id = audit(
                uow, self._clock, cmd.user_id, AuditAction.AI_CALL, target, parent_id, record.to_detail()
            )
            uow.commit()
        ai_metrics.info(
            "ai_call",
            extra={
                "node": cmd.node_name,
                "status": status,
                "model": model_name,
                "input_tokens": tokens[0] if tokens else None,
                "output_tokens": tokens[1] if tokens else None,
                "latency_ms": latency_ms,
            },
        )
        return ModelCallOutcome(status == AgentRunStatus.SUCCEEDED, audit_id, None, output)


# ===========================================================================
# UC-17 보관기한 경과·삭제 요청·탈퇴 파기 배치
# ===========================================================================
@dataclass(frozen=True)
class RetentionPolicy:
    """데이터별 보관 기간(v0.9 팀 결정 5, 잠정안). 법정 보존기간이 아니며 설정값으로 바꾼다.

    - deleted_case_grace_days : 사용자가 지운 사건의 복구 유예
    - unconverted_session_days: 사건으로 전환하지 않은 점검, 마지막 이용 후
    - closed_case_days        : 종결된 사건, 종결 후(결과 다운로드는 MVP 제외)
    - audit_log_days          : 감사 로그(실증 단계 잠정 90일). 탈퇴 계정의 빈 행도 이 기간 뒤 삭제
    탈퇴한 사용자의 자료는 기간 없이 '지체 없이' 파기한다.
    """

    deleted_case_grace_days: int = 30
    unconverted_session_days: int = 30
    closed_case_days: int = 30
    audit_log_days: int = 90


@dataclass(frozen=True)
class PurgeReport:
    cases: int
    documents: int
    files: int
    checkpoints: int
    sessions: int = 0  # 사건 없이 파기된 점검(미전환 점검·탈퇴자 점검)
    withdrawn_users: int = 0
    consents: int = 0
    audit_logs: int = 0
    accounts: int = 0  # 지운 탈퇴 계정 빈 행


class _Collector:
    """한 트랜잭션에서 지울 행을 지우면서, 커밋 뒤 지울 파일 키·thread_id 를 모은다."""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow
        self.files: list[str] = []
        self.threads: list[str] = []
        self.cases = 0
        self.sessions = 0

    def case(self, case_id: uuid.UUID) -> None:
        case = self.uow.cases.get(case_id)
        origin = case.origin_session_id if case else None
        self.threads += self.uow.purge.thread_ids_of(case_id, origin)
        self.files += [d.storage_key for d in self.uow.purge.documents_of_case(case_id)]
        self.uow.purge.hard_delete_case(case_id)
        self.cases += 1

    def session(self, session_id: uuid.UUID) -> None:
        self.threads += self.uow.purge.thread_ids_of(None, session_id)
        self.files += [d.storage_key for d in self.uow.purge.documents_of_session(session_id)]
        self.uow.purge.hard_delete_session(session_id)
        self.sessions += 1

    def user(self, user_id: uuid.UUID) -> int:
        """탈퇴자의 사건(원본 점검 포함) → 남은 점검 → 동의 이력 순으로 지운다. 지운 동의 건수를 돌려준다."""
        for case_id in self.uow.purge.case_ids_of_user(user_id):
            self.case(case_id)
        for session_id in self.uow.purge.session_ids_of_user(user_id):
            self.session(session_id)
        return self.uow.purge.delete_consents_of_user(user_id)


def _after_commit(storage: FileStorage, checkpoints: CheckpointStore, col: _Collector) -> tuple[int, int]:
    """DB 커밋 뒤 파일·체크포인트 삭제. DB 가 먼저 지워지므로 실패해도 '덜 남는' 쪽이다."""
    files = list(dict.fromkeys(col.files))
    threads = list(dict.fromkeys(col.threads))
    for key in files:
        storage.delete(key)
    for thread_id in threads:
        checkpoints.delete_thread(thread_id)
    return len(files), len(threads)


class PurgeExpiredData:
    """보관기간이 지난 자료를 '물리' 파기한다(DB-04, v0.9 데이터별 보관 정책).

    v0.8 묶음 보존(팀 결정 ⑤): 계약서(PDF)만 따로 지우지 않는다. 문서는 그것이 속한 점검·사건과
    같은 기록 묶음으로 보고 함께 파기한다. 문서의 retention_until 은 정보용이다.

    v0.9 처리 대상(한 트랜잭션):
      1) 탈퇴자 자료(기간 없음: 탈퇴 직후 파기가 실패했을 때의 재처리)
      2) 사용자가 지운 사건(유예 30일 후)       3) 종결 사건(종결 30일 후)
      4) 미전환 점검(마지막 이용 30일 후)        5) 감사 로그(90일 경과)
      6) 탈퇴 계정 빈 행(탈퇴 90일 후, 감사 로그가 더 이상 가리키지 않을 때)
    커밋 후 객체 저장소 파일·LangGraph 체크포인트를 지우고, 감사 로그에는 건수만 남긴다.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        storage: FileStorage,
        checkpoints: CheckpointStore,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._storage = storage
        self._checkpoints = checkpoints
        self._p = policy or RetentionPolicy()

    def execute(self) -> PurgeReport:
        now = self._clock.now()
        days = timedelta(days=1)
        with self._uow as uow:
            col = _Collector(uow)
            withdrawn = uow.purge.withdrawn_users_with_data()
            consents = sum(col.user(uid) for uid in withdrawn)
            for case_id in uow.purge.cases_deleted_before(now - self._p.deleted_case_grace_days * days):
                col.case(case_id)
            for case_id in uow.purge.cases_closed_before(now - self._p.closed_case_days * days):
                col.case(case_id)
            for session_id in uow.purge.idle_unconverted_sessions(
                now - self._p.unconverted_session_days * days
            ):
                col.session(session_id)
            logs = uow.purge.delete_audit_logs_before(now - self._p.audit_log_days * days)
            accounts = uow.purge.delete_withdrawn_users_before(now - self._p.audit_log_days * days)
            detail = {
                "cases": col.cases,
                "sessions": col.sessions,
                "withdrawn_users": len(withdrawn),
                "consents": consents,
                "audit_logs": logs,
                "accounts": accounts,
                "files": len(set(col.files)),
                "threads": len(set(col.threads)),
            }
            audit(uow, self._clock, None, AuditAction.PURGE, "batch", None, detail)
            uow.commit()
        files_n, threads_n = _after_commit(self._storage, self._checkpoints, col)
        return PurgeReport(
            col.cases, files_n, files_n, threads_n, col.sessions, len(withdrawn), consents, logs, accounts
        )


class PurgeUserData:
    """회원 탈퇴 직후 그 사용자 자료만 즉시 파기한다(파기 전용 접속). 실패해도 PurgeExpiredData 가 재처리."""

    def __init__(
        self, uow: UnitOfWork, clock: Clock, storage: FileStorage, checkpoints: CheckpointStore
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._storage = storage
        self._checkpoints = checkpoints

    def execute(self, user_id: uuid.UUID) -> PurgeReport:
        with self._uow as uow:
            user = uow.users.get(user_id)
            if user is None or user.status != UserStatus.WITHDRAWN:
                raise ValidationError("탈퇴한 계정만 즉시 파기할 수 있습니다.")
            col = _Collector(uow)
            consents = col.user(user_id)
            audit(
                uow,
                self._clock,
                None,
                AuditAction.PURGE,
                "users",
                user_id,
                {"reason": "WITHDRAWAL", "cases": col.cases, "sessions": col.sessions, "consents": consents},
            )
            uow.commit()
        files_n, threads_n = _after_commit(self._storage, self._checkpoints, col)
        return PurgeReport(col.cases, files_n, files_n, threads_n, col.sessions, 1, consents)
