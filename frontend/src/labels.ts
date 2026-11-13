import type { FactStatus, SourceKind } from "./api";

// 백엔드 run_syn001.LABELS와 같은 이름. 화면에서 새 상태·필드를 만들지 않는다.
export const FACT_LABEL: Record<string, string> = {
  BUSINESS_NAME: "사업자명",
  CONTRACT_PERIOD: "계약기간",
  PAYMENT_AMOUNT: "결제금액",
  PAYMENT_METHOD: "결제수단",
  TOTAL_SESSIONS: "총 이용횟수",
  USED_SESSIONS: "실제 이용횟수",
  CONTRACT_DATE: "계약일",
  CANCEL_REQUEST_DATE: "해지 요청일",
  BUSINESS_RESPONSE: "사업자 답변",
  REFUND_CLAUSE: "환불 조항",
  OTHER: "계약서 사본 보유 여부",
};

export const STATUS_LABEL: Record<FactStatus, { text: string; tone: string }> = {
  EXTRACTED: { text: "미처리", tone: "red" },
  CONFIRMED: { text: "확인함", tone: "green" },
  MODIFIED: { text: "고쳐서 확인", tone: "purple" },
  REJECTED: { text: "틀림 표시", tone: "gray" },
  ACKNOWLEDGED_UNKNOWN: { text: "모름으로 남김", tone: "gray" },
};

export function sourceLabel(kind: SourceKind, page: number | null): { text: string; tone: string } {
  if (kind === "DOCUMENT") return { text: `계약서 원문${page ? ` p.${page}` : ""}`, tone: "green" };
  if (kind === "STATEMENT") return { text: "사용자 진술", tone: "orange" };
  return { text: "미확인", tone: "red" };
}

export const CATEGORY_LABEL = {
  CONFIRMED_FACT: "확인된 사실",
  UNVERIFIED: "미확인 정보",
  CAUTION: "주의 · 보류",
} as const;

export const HOLD_LABEL: Record<string, string> = {
  NO_CITATION: "인용 근거 없음 · 팀 인용 승인 전",
  CITATION_FAILED: "인용 검증 실패",
};
