"""컴포지션 루트(Composition Root).

'어떤 구현체를 어떤 유스케이스에 꽂을지'를 결정하는 유일한 장소다.
다른 모든 코드는 추상(포트)에만 의존하고, 구체 클래스의 조립은 여기서만 한다(DIP).

요청마다 새로운 UnitOfWork 를 만들어 주입한다 → 동시 요청 간 DB 세션 공유를 막는다.
"""

from __future__ import annotations

import copy
import uuid

from sqlalchemy.orm import Session, sessionmaker

from app.application.ports.services import (
    AgencyDirectory,
    CheckpointStore,
    Clock,
    ExternalModelClient,
    FileStorage,
    KnowledgeBase,
    PasswordHasher,
    UnitOfWork,
)
from app.application.use_cases import account as uc_account
from app.application.use_cases import agent as uc_agent
from app.application.use_cases import case as uc_case
from app.application.use_cases import check_session as uc_check
from app.application.use_cases import report as uc_report
from app.domain.policies import (
    CitationVerifier,
    ExternalTransferPolicy,
    LegalExpressionPolicy,
    PersonalInfoMasker,
    ReportCompositionPolicy,
    UploadPolicy,
)
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.knowledge import JsonAgencyDirectory, LocalKnowledgeBase
from app.infrastructure.services import (
    Argon2PasswordHasher,
    DisabledModelClient,
    JwtTokenVerifier,
    ServiceTokenVerifier,
)


class Container:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        storage: FileStorage,
        token_verifier: JwtTokenVerifier,
        retention_days: int = 180,
        model_client: ExternalModelClient | None = None,
        checkpoints: CheckpointStore | None = None,
        entrustment_enabled: bool = False,
        purge_grace_days: int = 30,
        knowledge: KnowledgeBase | None = None,
        agencies: AgencyDirectory | None = None,
        batch_session_factory: sessionmaker[Session] | None = None,
        *,
        auth_session_factory: sessionmaker[Session] | None = None,
        service_verifier: ServiceTokenVerifier | None = None,
        password_hasher: PasswordHasher | None = None,
        login_lock: uc_account.LoginLockPolicy | None = None,
        retention: uc_agent.RetentionPolicy | None = None,
        access_token_minutes: int = 60,
        public_signup_enabled: bool = False,
    ) -> None:
        self._factory = session_factory
        # v0.8 RLS: 파기 배치처럼 모든 사용자 행을 다루는 작업은 RLS 우회 전용 계정(BYPASSRLS)으로만 접속
        self._batch_factory = batch_session_factory or session_factory
        # v0.9: 로그인(누구인지 모르는 시점의 users 조회·가입)은 인증 전용 접속(app_auth)으로만
        self._auth_factory = auth_session_factory or self._batch_factory
        # v0.9 R-01: AI 내부 API 는 시민 토큰이 아니라 별도 비밀키의 서비스 토큰으로만 호출
        self.service_verifier = service_verifier or ServiceTokenVerifier(None)
        # v0.9 이메일·비밀번호 로그인: Argon2id 해시, 연속 실패 잠금
        self._hasher = password_hasher or Argon2PasswordHasher()
        self._lock = login_lock or uc_account.LoginLockPolicy()
        self.access_token_minutes = access_token_minutes
        # v0.11 SUP-01: 공개 회원가입은 기본 닫힘. 닫혀 있어도 sign_up() 유스케이스 자체는 남겨
        # 권한 있는 발급 스크립트(인증 전용 DB 접속)가 같은 검증·해시 절차를 쓰게 한다.
        self.public_signup_enabled = public_signup_enabled
        self._acting_user_id: uuid.UUID | None = None
        self.clock = clock
        self.storage = storage
        self.token_verifier = token_verifier
        self._retention_days = retention_days
        # 외부 모델은 명시적으로 설정해야만 쓰인다(기본값은 '호출 불가' 구현 → 데이터 유출 방지)
        self.model_client: ExternalModelClient = model_client or DisabledModelClient()
        self.checkpoints = checkpoints
        # v0.9: 보관 기간은 설정값(RetentionPolicy). purge_grace_days 는 v0.8 호환용 인자
        self._retention = retention or uc_agent.RetentionPolicy(deleted_case_grace_days=purge_grace_days)
        # 업무 DB 밖의 저장소(v0.6). 미설정이면 '비어 있는' 구현 → 인용은 모두 CHUNK_NOT_FOUND 로 보류된다
        self.knowledge: KnowledgeBase = knowledge or LocalKnowledgeBase([], [])
        self.agencies: AgencyDirectory = agencies or JsonAgencyDirectory([])
        self._verifier = CitationVerifier()
        self._transfer_policy = ExternalTransferPolicy(entrustment_enabled=entrustment_enabled)
        # 상태가 없는 정책 객체는 공유해도 안전하다.
        self._legal = LegalExpressionPolicy()
        self._upload = UploadPolicy()
        self._masker = PersonalInfoMasker()
        self._report_policy = ReportCompositionPolicy(self._legal)

    def for_user(self, user_id: uuid.UUID) -> Container:
        """요청 사용자에 묶인 얕은 사본. 이 사본이 만드는 UoW 는 DB 세션에 사용자를 알린다(RLS)."""
        bound = copy.copy(self)
        bound._acting_user_id = user_id
        return bound

    def _uow(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self._factory, self._acting_user_id)

    # --- 계정(v0.9) --------------------------------------------------------
    def sign_up(self) -> uc_account.SignUp:
        return uc_account.SignUp(SqlAlchemyUnitOfWork(self._auth_factory), self.clock, self._hasher)

    def login(self) -> uc_account.Login:
        return uc_account.Login(
            SqlAlchemyUnitOfWork(self._auth_factory), self.clock, self._hasher, self._lock
        )

    def ensure_active_user(self) -> uc_account.EnsureActiveUser:
        return uc_account.EnsureActiveUser(self._uow())

    def withdraw_account(self) -> uc_account.WithdrawAccount:
        return uc_account.WithdrawAccount(self._uow(), self.clock, self._hasher, self._lock)

    def purge_user_data(self) -> uc_agent.PurgeUserData:
        # v0.11 SUP-03: 체크포인트 저장소가 연결되지 않아도 DB·파일 파기는 진행한다.
        # 체크포인트 대상(thread_id)은 purge_tasks 에 남고, 결과는 '미완료(NOT_CONNECTED)'로 보고된다.
        return uc_agent.PurgeUserData(
            SqlAlchemyUnitOfWork(self._batch_factory), self.clock, self.storage, self.checkpoints
        )

    # --- 점검 -------------------------------------------------------------
    def record_consent(self) -> uc_check.RecordConsent:
        return uc_check.RecordConsent(self._uow(), self.clock)

    def start_check_session(self) -> uc_check.StartCheckSession:
        return uc_check.StartCheckSession(self._uow(), self.clock, self._masker)

    def upload_document(self) -> uc_check.UploadContractDocument:
        return uc_check.UploadContractDocument(
            self._uow(), self.clock, self.storage, self._upload, self._retention_days
        )

    def record_facts(self) -> uc_check.RecordExtractedFacts:
        return uc_check.RecordExtractedFacts(self._uow(), self.clock)

    def confirm_fact(self) -> uc_check.ConfirmFact:
        return uc_check.ConfirmFact(self._uow(), self.clock)

    def list_session_facts(self) -> uc_check.ListSessionFacts:
        return uc_check.ListSessionFacts(self._uow(), self.clock)

    # --- 보고서 -----------------------------------------------------------
    def submit_report(self) -> uc_report.SubmitInspectionReport:
        return uc_report.SubmitInspectionReport(
            self._uow(), self.clock, self._report_policy, self._verifier, self.knowledge
        )

    def approve_report(self) -> uc_report.ApproveInspectionReport:
        return uc_report.ApproveInspectionReport(self._uow(), self.clock)

    def get_report_detail(self) -> uc_report.GetReportDetail:
        return uc_report.GetReportDetail(self._uow(), self.clock)

    # --- 사건 -------------------------------------------------------------
    def convert_to_case(self) -> uc_case.ConvertSessionToCase:
        return uc_case.ConvertSessionToCase(self._uow(), self.clock)

    def create_case(self) -> uc_case.CreateCase:
        return uc_case.CreateCase(self._uow(), self.clock)

    def attach_case_document(self) -> uc_case.AttachCaseDocument:
        return uc_case.AttachCaseDocument(
            self._uow(), self.clock, self.storage, self._upload, self._retention_days
        )

    def add_case_event(self) -> uc_case.AddCaseEvent:
        return uc_case.AddCaseEvent(self._uow(), self.clock)

    def register_evidence(self) -> uc_case.RegisterEvidence:
        return uc_case.RegisterEvidence(self._uow(), self.clock)

    def prepare_consultation(self) -> uc_case.PrepareConsultation:
        return uc_case.PrepareConsultation(self._uow(), self.clock, self._legal, self.agencies)

    def get_case_detail(self) -> uc_case.GetCaseDetail:
        return uc_case.GetCaseDetail(self._uow(), self.clock)

    def delete_case(self) -> uc_case.DeleteCase:
        return uc_case.DeleteCase(self._uow(), self.clock)

    # --- AI 실행·파기 -----------------------------------------------------
    def start_agent_thread(self) -> uc_agent.StartAgentThread:
        return uc_agent.StartAgentThread(self._uow(), self.clock)

    def invoke_external_model(self) -> uc_agent.InvokeExternalModel:
        return uc_agent.InvokeExternalModel(
            self._uow(), self.clock, self.model_client, self._masker, self._transfer_policy
        )

    def purge_expired_data(self) -> uc_agent.PurgeExpiredData:
        return uc_agent.PurgeExpiredData(
            SqlAlchemyUnitOfWork(self._batch_factory),
            self.clock,
            self.storage,
            self.checkpoints,
            self._retention,
        )
