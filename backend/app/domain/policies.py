"""도메인 정책(업무 규칙) 모음.

각 정책은 '한 가지 판단'만 책임진다(SRP).
정책을 바꾸거나 추가할 때 유스케이스 코드를 고치지 않아도 되도록,
유스케이스는 정책 객체를 생성자로 주입받는다(OCP·DIP).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.domain.entities import ExtractedFact, ReportItem
from app.domain.enums import (
    CitationCheck,
    FactStatus,
    GuardCode,
    HoldReason,
    ReportCategory,
    TransferBasis,
)
from app.domain.errors import ValidationError


# ---------------------------------------------------------------------------
# 1) 파일 업로드 정책
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UploadPolicy:
    """계약서 업로드 허용 규칙.

    - MVP 범위는 '텍스트 PDF' 이므로 PDF 만 허용(OCR 은 선택 기능).
    - 확장자·Content-Type 은 위조가 쉬우므로 파일 앞부분의 '매직 바이트'까지 확인한다.
    """

    max_bytes: int = 10 * 1024 * 1024  # 10MB
    allowed_mime_types: frozenset[str] = frozenset({"application/pdf"})
    pdf_magic: bytes = b"%PDF-"

    def validate(self, filename: str, mime_type: str, content: bytes) -> None:
        if not content:
            raise ValidationError("빈 파일은 업로드할 수 없습니다.")
        if len(content) > self.max_bytes:
            raise ValidationError(f"파일 크기는 {self.max_bytes // (1024 * 1024)}MB 이하여야 합니다.")
        if mime_type not in self.allowed_mime_types:
            raise ValidationError("PDF 파일만 업로드할 수 있습니다.")
        if not content.startswith(self.pdf_magic):
            raise ValidationError("PDF 형식이 아닌 파일입니다.")
        if not filename.lower().endswith(".pdf"):
            raise ValidationError("파일 확장자는 .pdf 여야 합니다.")

    @staticmethod
    def safe_display_name(filename: str) -> str:
        """경로 조작(../) 과 제어문자를 제거한 '표시용' 파일명. 저장 경로에는 절대 쓰지 않는다."""
        name = filename.replace("\\", "/").split("/")[-1]
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
        return name[:200] or "document.pdf"


# ---------------------------------------------------------------------------
# 2) 개인정보 마스킹 정책
# ---------------------------------------------------------------------------
class PersonalInfoMasker:
    """시민이 자유 입력한 상황 설명에서 불필요한 고유식별정보를 저장 전에 가린다.

    서비스 목적상 필요 없는 정보(주민등록번호, 카드번호, 계좌번호, 휴대전화)는
    '최소 수집' 원칙에 따라 DB 에 원문으로 남기지 않는다.
    """

    _PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"\b\d{6}\s?-\s?[1-4]\d{6}\b"), "[주민등록번호]"),
        (re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"), "[카드번호]"),
        (re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b"), "[휴대전화]"),
        (re.compile(r"\b\d{2,6}-\d{2,6}-\d{2,8}\b"), "[계좌번호]"),
    )

    _LABELS = ("[주민등록번호]", "[카드번호]", "[휴대전화]", "[계좌번호]")

    def mask(self, text: str) -> str:
        for pattern, label in self._PATTERNS:
            text = pattern.sub(label, text)
        return text

    @classmethod
    def count_masks(cls, text: str) -> int:
        """마스킹으로 가린 개수(민감정보 '과다 포함' 판단에 사용)."""
        return sum(text.count(label) for label in cls._LABELS)


# ---------------------------------------------------------------------------
# 3) 법률 표현 통제 정책
# ---------------------------------------------------------------------------
class LegalExpressionPolicy:
    """AI 가 법률 결론을 '단정'하는 문장을 차단한다.

    기획서 범위: 위법 여부, 확정 환불액, 승소율, 소송 전략 제시는 제외.
    (변호사법상 법률사무 경계 리스크 대응 / 품질 기준 '금지 법률 판단 0건')
    """

    _FORBIDDEN: tuple[re.Pattern[str], ...] = (
        re.compile(r"승소"),
        re.compile(r"패소"),
        re.compile(r"위법(입니다|이다|합니다)"),
        re.compile(r"불법(입니다|이다)"),
        re.compile(r"반드시\s*환불"),
        re.compile(r"환불\s*받을\s*수\s*있습니다"),
        re.compile(r"(확정|최종)\s*환불\s*(액|금액)"),
        re.compile(r"소송(을|하)\s*(하세요|권합니다|제기하십시오)"),
    )

    def violations(self, text: str) -> list[str]:
        return [p.pattern for p in self._FORBIDDEN if p.search(text)]

    def validate(self, text: str) -> None:
        found = self.violations(text)
        if found:
            raise ValidationError("법률 판단을 단정하는 표현이 포함되어 있습니다: " + ", ".join(found))


# ---------------------------------------------------------------------------
# 4) 점검 보고서 구성 정책
# ---------------------------------------------------------------------------
class ReportCompositionPolicy:
    """3분류 점검 보고서가 갖춰야 할 조건.

    - 모든 항목: 법률 결론 단정 표현이 있으면 저장 자체를 거부한다(출력 전 검사, FR-REP-002).
    - 확인된 사실(CONFIRMED_FACT): 사용자가 승인한 사실(fact_id)에 연결되어야 한다.
      → 승인하지 않은 AI 추정이 '확인된 사실'로 저장되지 않는다(AI-01 완료 기준).
    - 주의·추가 확인(CAUTION): 서버 검증을 통과한 인용이 1건 이상 없으면 '보류'로 돌린다.
      → 근거 부족·가짜 출처는 거부 대신 보류로 남겨, 재검색 후 새 버전 보고서로 보완한다(FR-RAG-002).
    - 미확인 정보(UNVERIFIED): 근거 없이도 기록 가능('모른다'는 사실을 남기는 칸).
    """

    def __init__(self, legal_policy: LegalExpressionPolicy) -> None:
        self._legal = legal_policy

    # v0.11 SUP-02(요약문 전용): 요약문에는 인용을 붙일 자리가 없으므로 항목보다 좁게 허용한다.
    # ① 환급·환불 '결과'를 말하는 문장은 인용이 있어도 요약에 쓰지 않는다(결과는 기관·당사자 확인 사항).
    _SUMMARY_OUTCOME: tuple[re.Pattern[str], ...] = (
        re.compile(r"(돌려|환급|환불)\s*받(을\s*수\s*있|게\s*됩니다|으실\s*수)"),
        re.compile(r"\d[\d,.]*\s*(%|퍼센트|원)\S*\s*(을|를)?\s*(환급|환불|돌려)"),
        re.compile(r"(환급|환불)\s*(대상|가능)\s*(입니다|이다|합니다)"),
        re.compile(r"(전액|일부)\s*(환급|환불)"),
    )
    # ② 법령·기준을 언급하는 요약은, 같은 보고서에 서버가 검증한(보류 아님) '주의' 항목이 있을 때만 허용한다.
    _SUMMARY_LEGAL_SOURCE = re.compile(
        r"(법률|법령|기본법|거래법|판매법|규제법|민법|형법|시행령|시행규칙|고시|약관|분쟁\s*해결\s*기준|별표|제\s*\d+\s*조)"
    )

    def validate_summary(self, summary_text: str) -> None:
        """v0.11 SUP-02: 요약문도 항목과 같은 금지 표현 검사 + 요약 전용 결과 단정 검사(저장 전)."""
        self._legal.validate(summary_text)
        found = [p.pattern for p in self._SUMMARY_OUTCOME if p.search(summary_text)]
        if found:
            raise ValidationError("요약문에는 환급·환불 결과를 쓰지 않습니다(근거 항목·상담으로 확인).")

    def validate_summary_grounding(self, summary_text: str, items: Iterable[ReportItem]) -> None:
        """v0.11 SUP-02: 법령·기준을 언급한 요약은 검증된 '주의' 항목이 1건 이상일 때만 저장한다.

        인용 검증(apply) 뒤에 부른다. 근거가 모두 보류이면 요약도 그 근거를 말할 수 없다.
        """
        if not self._SUMMARY_LEGAL_SOURCE.search(summary_text):
            return
        grounded = any(i.category == ReportCategory.CAUTION and not i.is_held for i in items)
        if not grounded:
            raise ValidationError(
                "요약문이 법령·기준을 언급하지만 서버가 검증한 '주의' 항목이 없습니다."
                " 근거 항목을 먼저 확인하세요."
            )

    def apply(self, items: Iterable[ReportItem], facts: Iterable[ExtractedFact]) -> list[ReportItem]:
        """규칙 위반은 예외, 근거 부족은 hold_reason 을 채워 돌려준다."""
        items = list(items)
        if not items:
            raise ValidationError("보고서 항목이 비어 있습니다.")
        facts = list(facts)
        session_fact_ids = {f.id for f in facts}
        confirmed_ids = {f.id for f in facts if f.is_confirmed}
        # v0.11: 사용자가 '모름'으로 인지한 항목은 빠짐없이 '미확인 정보'로 보고서에 나와야 한다
        ack_ids = {f.id for f in facts if f.status == FactStatus.ACKNOWLEDGED_UNKNOWN}
        for item in items:
            self._legal.validate(item.content)
            if item.fact_id is not None and item.fact_id not in session_fact_ids:
                raise ValidationError("보고서 항목은 이 점검의 사실만 가리킬 수 있습니다.")
            if item.fact_id in ack_ids and item.category != ReportCategory.UNVERIFIED:
                raise ValidationError("'모름'으로 처리한 항목은 '미확인 정보'로만 쓸 수 있습니다.")
            if item.category == ReportCategory.CONFIRMED_FACT:
                if item.fact_id is None or item.fact_id not in confirmed_ids:
                    raise ValidationError("'확인된 사실' 항목은 사용자가 승인한 사실과 연결되어야 합니다.")
            elif item.category == ReportCategory.CAUTION:
                if not item.citations:
                    item.hold_reason = HoldReason.NO_CITATION
                elif not any(c.is_verified for c in item.citations):
                    item.hold_reason = HoldReason.CITATION_FAILED
        listed = {i.fact_id for i in items if i.category == ReportCategory.UNVERIFIED}
        missing = ack_ids - listed
        if missing:
            raise ValidationError(
                f"'모름'으로 처리한 항목 {len(missing)}건이 '미확인 정보'(fact_id 연결)로 들어 있지 않습니다."
            )
        return items


# ---------------------------------------------------------------------------
# 5) 인용 검증 정책 (AI-03)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ChunkFacts:
    """인용 검증에 필요한 청크 정보(벡터 지식 DB 에서 읽어 온 값).

    제목·URL·조항은 보고서의 '인용 당시 출처'로 보존한다. 생산 경로는 인용문 일치
    검사 전에 별도 승인 어댑터에서 출처 ID·URL·버전과 본문 해시를 대조한다.
    """

    content: str
    source_active: bool
    effective_date: date | None
    source_title: str | None = None
    source_url: str | None = None
    article_ref: str | None = None
    # 팀 인용 승인 대조용 출처 식별값. 기존 인용 판정 enum·API 응답은 바꾸지 않는다.
    source_id: str | None = None
    source_version: str | None = None


class CitationVerifier:
    """인용이 '진짜 근거'인지 서버가 판정한다. 클라이언트·LLM 이 보낸 '검증됨' 값은 쓰지 않는다.

    판정 순서: ① 청크 실재 → ② 출처 활성(폐지·구버전 아님) → ③ 시행일 도래 → ④ 인용문이 원문에 존재
    이 판정은 팀 인용 승인이나 개별 사건의 적용성 판단을 대체하지 않는다.
    """

    _WS = re.compile(r"\s+")

    def _norm(self, text: str) -> str:
        return self._WS.sub("", text)

    def check(self, chunk: ChunkFacts | None, quoted_text: str, today: date) -> CitationCheck:
        if chunk is None:
            return CitationCheck.CHUNK_NOT_FOUND
        if not chunk.source_active:
            return CitationCheck.SOURCE_INACTIVE
        if chunk.effective_date is not None and chunk.effective_date > today:
            return CitationCheck.NOT_YET_EFFECTIVE
        quote = self._norm(quoted_text)
        if not quote or quote not in self._norm(chunk.content):
            return CitationCheck.QUOTE_MISMATCH
        return CitationCheck.VERIFIED


# ---------------------------------------------------------------------------
# 6) 외부 전송 가드 정책 (InputGuard, AI-02)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TransferDecision:
    allowed: bool
    basis: TransferBasis
    code: GuardCode | None = None


class ExternalTransferPolicy:
    """외부(해외 포함) 모델 API 로 보내기 '전에' 전송 가능 여부를 판정한다(fail-closed).

    - 처리 근거: 운영 설정상 처리위탁(ENTRUSTMENT)이 확정돼 있거나, 국외 이전 동의(CONSENT)가 유효해야 한다.
    - 철회: 동의를 철회했다면 위탁 설정과 무관하게 차단한다(정보주체 의사 우선 — 운영 정책, 법률 검토 대상).
    - 최소화: 마스킹 후 텍스트만 보내며, 길이 상한·마스킹 개수 상한을 넘으면 차단한다.
    판정이 애매하면 '차단'으로 기운다. 차단 시 외부 호출은 0회다.
    """

    def __init__(self, entrustment_enabled: bool = False, max_chars: int = 6000, max_masked: int = 3) -> None:
        self._entrustment = entrustment_enabled
        self._max_chars = max_chars
        self._max_masked = max_masked

    def decide(self, consent_effective: bool, consent_withdrawn: bool, masked_text: str) -> TransferDecision:
        if consent_withdrawn:
            return TransferDecision(False, TransferBasis.NOT_APPLICABLE, GuardCode.CONSENT_WITHDRAWN)
        if consent_effective:
            basis = TransferBasis.CONSENT
        elif self._entrustment:
            basis = TransferBasis.ENTRUSTMENT
        else:
            return TransferDecision(False, TransferBasis.NOT_APPLICABLE, GuardCode.NO_TRANSFER_BASIS)
        if len(masked_text) > self._max_chars:
            return TransferDecision(False, TransferBasis.NOT_APPLICABLE, GuardCode.INPUT_TOO_LARGE)
        if PersonalInfoMasker.count_masks(masked_text) > self._max_masked:
            return TransferDecision(False, TransferBasis.NOT_APPLICABLE, GuardCode.SENSITIVE_DATA)
        return TransferDecision(True, basis)


# ---------------------------------------------------------------------------
# v0.9 회원가입: 이메일·비밀번호 규칙
# ---------------------------------------------------------------------------
class CredentialPolicy:
    """이메일 정규화와 비밀번호 작성 규칙.

    비밀번호 규칙은 KISA 「암호이용 안내서」·개인정보 안전성 확보조치 해설서에서 흔히 쓰는 기준을 따른다:
    문자 종류(영대문자·영소문자·숫자·특수문자) 2종 이상이면 10자 이상, 3종 이상이면 8자 이상. 최대 64자.
    이메일 앞부분(아이디)을 그대로 포함하는 비밀번호는 거부한다.
    """

    MAX_PASSWORD = 64
    _EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

    @classmethod
    def normalize_email(cls, email: str) -> str:
        value = email.strip().lower()
        if len(value) > 254 or not cls._EMAIL.match(value):
            raise ValidationError("이메일 형식이 올바르지 않습니다.")
        return value

    @classmethod
    def validate_password(cls, password: str, email: str) -> None:
        if len(password) > cls.MAX_PASSWORD:
            raise ValidationError(f"비밀번호는 {cls.MAX_PASSWORD}자 이하여야 합니다.")
        kinds = sum(
            (
                any(c.isupper() and c.isascii() for c in password),
                any(c.islower() and c.isascii() for c in password),
                any(c.isdigit() for c in password),
                any(not c.isalnum() for c in password),
            )
        )
        if not ((kinds >= 3 and len(password) >= 8) or (kinds >= 2 and len(password) >= 10)):
            raise ValidationError(
                "비밀번호는 영문 대·소문자·숫자·특수문자 중 3종 이상 8자 이상, "
                "또는 2종 이상 10자 이상이어야 합니다."
            )
        local = email.split("@", 1)[0]
        if len(local) >= 4 and local in password.lower():
            raise ValidationError("비밀번호에 이메일 아이디를 넣을 수 없습니다.")
