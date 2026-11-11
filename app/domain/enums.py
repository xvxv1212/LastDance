"""도메인 코드값(열거형).

DB 에는 문자열 코드로 저장되며(CHECK 제약), 테이블명세서의 '코드값' 열과 1:1 로 대응한다.
StrEnum 을 사용하여 JSON 직렬화·DB 저장 시 별도 변환이 필요 없도록 했다.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """사용자 역할."""

    CITIZEN = "CITIZEN"  # 일반 시민
    COUNSELOR = "COUNSELOR"  # 상담기관 담당자(열람 권한은 사용자 공유 시에만)
    ADMIN = "ADMIN"  # 운영 관리자


class UserStatus(StrEnum):
    """계정 상태."""

    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    WITHDRAWN = "WITHDRAWN"


class ConsentType(StrEnum):
    """개인정보 동의 종류."""

    PRIVACY_REQUIRED = "PRIVACY_REQUIRED"  # 필수 개인정보 수집·이용
    SENSITIVE_DOCUMENT = "SENSITIVE_DOCUMENT"  # 계약서·증빙 등 문서 업로드
    OVERSEAS_TRANSFER = "OVERSEAS_TRANSFER"  # 해외 LLM API 로의 국외 이전


class EntryType(StrEnum):
    """첫 화면 진입 경로(기획서 '시민 진입 경로' 3개 버튼)."""

    REFUND_CONCERN = "REFUND_CONCERN"  # 환불·해지가 고민돼요
    DISPUTE_OCCURRED = "DISPUTE_OCCURRED"  # 이미 분쟁이 생겼어요
    PRE_CONTRACT = "PRE_CONTRACT"  # 계약 전에 확인하고 싶어요


class SessionStatus(StrEnum):
    """AI 점검 세션 상태(LangGraph 흐름의 진행 단계와 대응)."""

    DRAFT = "DRAFT"  # 상황 입력 중
    FACTS_EXTRACTED = "FACTS_EXTRACTED"  # 1차 사실 추출 완료(사용자 확인 대기)
    FACTS_CONFIRMED = "FACTS_CONFIRMED"  # 사용자가 사실 확인 완료
    REPORTED = "REPORTED"  # 점검 보고서 승인 완료
    CONVERTED = "CONVERTED"  # 사건으로 전환됨
    ABANDONED = "ABANDONED"  # 사용자가 중단


class FactKey(StrEnum):
    """구조화 대상 사실 항목(필라테스·헬스 회원권 시나리오 기준)."""

    CONTRACT_DATE = "CONTRACT_DATE"
    CONTRACT_PERIOD = "CONTRACT_PERIOD"
    PAYMENT_AMOUNT = "PAYMENT_AMOUNT"
    PAYMENT_METHOD = "PAYMENT_METHOD"  # 결제 수단(빅데이터 분석 정의서 Snapshot 필수 8항목)
    TOTAL_SESSIONS = "TOTAL_SESSIONS"
    USED_SESSIONS = "USED_SESSIONS"
    CANCEL_REQUEST_DATE = "CANCEL_REQUEST_DATE"
    BUSINESS_NAME = "BUSINESS_NAME"
    BUSINESS_RESPONSE = "BUSINESS_RESPONSE"
    REFUND_CLAUSE = "REFUND_CLAUSE"
    OTHER = "OTHER"


class FactSourceKind(StrEnum):
    """사실의 출처 종류."""

    DOCUMENT = "DOCUMENT"  # 업로드 문서(계약서 등)
    STATEMENT = "STATEMENT"  # 시민 진술
    UNKNOWN = "UNKNOWN"  # 미확인(문서·진술 어디에도 근거 없음 → 값은 비워 둔다)


class FactStatus(StrEnum):
    """추출 사실의 확인 상태."""

    EXTRACTED = "EXTRACTED"  # AI 가 추출, 아직 미확인
    CONFIRMED = "CONFIRMED"  # 사용자가 그대로 확인
    MODIFIED = "MODIFIED"  # 사용자가 수정 후 확인
    REJECTED = "REJECTED"  # 사용자가 틀렸다고 표시


class ConfirmAction(StrEnum):
    """사용자 확인 행위."""

    CONFIRM = "CONFIRM"
    MODIFY = "MODIFY"
    REJECT = "REJECT"


class ReportStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class ReportCategory(StrEnum):
    """점검 보고서 3분류(기획서 '3분류 점검 보고서')."""

    CONFIRMED_FACT = "CONFIRMED_FACT"  # 확인된 사실
    CAUTION = "CAUTION"  # 주의·추가 확인 필요사항
    UNVERIFIED = "UNVERIFIED"  # 미확인 정보


class OfficialSourceType(StrEnum):
    """공식 지식베이스 출처 종류."""

    LAW = "LAW"  # 법령
    DISPUTE_STANDARD = "DISPUTE_STANDARD"  # 소비자분쟁해결기준
    STANDARD_TERMS = "STANDARD_TERMS"  # 표준약관
    AGENCY_GUIDE = "AGENCY_GUIDE"  # 기관 공식 안내


class CaseType(StrEnum):
    FITNESS_REFUND = "FITNESS_REFUND"  # 체육시설(필라테스·헬스) 환불
    OTHER_CONSUMER = "OTHER_CONSUMER"


class CaseStatus(StrEnum):
    OPEN = "OPEN"  # 사건 생성
    PREPARING = "PREPARING"  # 상담 준비 중
    CONSULTING = "CONSULTING"  # 기관 상담 진행
    CLOSED = "CLOSED"  # 종결


class EventActor(StrEnum):
    """타임라인 사건의 행위 주체."""

    USER = "USER"
    BUSINESS = "BUSINESS"
    AGENCY = "AGENCY"
    OTHER = "OTHER"


class EvidenceType(StrEnum):
    CONTRACT = "CONTRACT"
    PAYMENT_RECEIPT = "PAYMENT_RECEIPT"
    MESSAGE = "MESSAGE"
    CALL_RECORD = "CALL_RECORD"
    PHOTO = "PHOTO"
    NOTICE = "NOTICE"
    OTHER = "OTHER"


class EvidenceStatus(StrEnum):
    HELD = "HELD"  # 보유
    MISSING = "MISSING"  # 미보유(준비 필요)
    REQUESTED = "REQUESTED"  # 사업자 등에 요청함


class UnverifiedStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class PrepStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"  # Human-in-the-loop 대기
    BLOCKED = "BLOCKED"  # InputGuard 가 외부 호출 전에 차단(fail-closed)


class TransferBasis(StrEnum):
    """외부(해외 포함) AI API 로 데이터를 보낼 때의 적법한 처리 근거.

    기획서 InputGuard: '외부 API 전송 필요성 및 적법한 처리 근거 확인'
    품질 기준: '적법한 근거 없는 외부 전송 0건'
    """

    NOT_APPLICABLE = "NOT_APPLICABLE"  # 외부 전송 없음(자체·국내 처리)
    CONSENT = "CONSENT"  # 정보주체 동의(user_consents 의 OVERSEAS_TRANSFER 등)
    ENTRUSTMENT = "ENTRUSTMENT"  # 처리위탁 계약·고지에 근거


class GuardCode(StrEnum):
    """InputGuard 차단 사유 코드. 원문 대신 이 코드만 로그에 남긴다(AI-02)."""

    NO_TRANSFER_BASIS = "NO_TRANSFER_BASIS"  # 동의·위탁 등 처리 근거 없음
    CONSENT_WITHDRAWN = "CONSENT_WITHDRAWN"  # 국외 이전 동의를 철회함
    SENSITIVE_DATA = "SENSITIVE_DATA"  # 마스킹 후에도 민감정보가 과다
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"  # 최소화 기준 초과(전송 상한)
    THREAD_NOT_OWNED = "THREAD_NOT_OWNED"  # 타인·삭제된 thread 접근


class CitationCheck(StrEnum):
    """서버가 인용을 검증한 결과(AI-03). 클라이언트가 보낸 '검증됨' 값은 믿지 않는다."""

    VERIFIED = "VERIFIED"  # 청크 실재·활성·시행 중·인용문 일치
    CHUNK_NOT_FOUND = "CHUNK_NOT_FOUND"  # 지식베이스에 없는 청크(가짜 출처)
    SOURCE_INACTIVE = "SOURCE_INACTIVE"  # 폐지·구버전 출처
    NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"  # 시행일 전
    QUOTE_MISMATCH = "QUOTE_MISMATCH"  # 인용문이 청크 원문에 없음


class HoldReason(StrEnum):
    """보고서 항목 '보류' 사유. 보류 항목은 확정 내용으로 표시하지 않는다."""

    NO_CITATION = "NO_CITATION"  # 근거 인용 없음
    CITATION_FAILED = "CITATION_FAILED"  # 인용 검증 실패


class AuditAction(StrEnum):
    CREATE = "CREATE"
    READ_SENSITIVE = "READ_SENSITIVE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    EXPORT = "EXPORT"
    CONSENT = "CONSENT"
    PURGE = "PURGE"  # 보관기한 경과·삭제 요청에 따른 물리 파기
    AI_CALL = "AI_CALL"  # 외부 AI 호출·차단 기록(v0.6: agent_runs 테이블 대신, 원문 없이 코드만)
