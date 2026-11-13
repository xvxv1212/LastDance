// 시민 API만 호출한다. AI 내부 API(POST /check-sessions/{id}/facts, /reports)는 호출하지 않는다.
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://127.0.0.1:8000";
const PREFIX = "/api/v1";

export type SessionStatus = "DRAFT" | "FACTS_EXTRACTED" | "FACTS_CONFIRMED" | "REPORTED" | "CONVERTED" | "ABANDONED";
export type FactStatus = "EXTRACTED" | "CONFIRMED" | "MODIFIED" | "REJECTED" | "ACKNOWLEDGED_UNKNOWN";
export type SourceKind = "DOCUMENT" | "STATEMENT" | "UNKNOWN";
export type ConfirmAction = "CONFIRM" | "MODIFY" | "REJECT" | "ACKNOWLEDGE_UNKNOWN";

export interface Fact {
  id: string;
  fact_key: string;
  ai_value: string | null;
  current_value: string | null;
  status: FactStatus;
  confirmed_at: string | null;
  has_conflict: boolean;
  source_kind: SourceKind;
  document_id: string | null;
  page_no: number | null;
}
export interface SessionFacts {
  check_session_id: string;
  session_status: SessionStatus;
  facts_confirmed_at: string | null;
  facts: Fact[];
}
export interface Citation {
  quoted_text: string | null;
  check_result: string;
  source_title: string | null;
  source_url: string | null;
  article_ref: string | null;
  effective_date: string | null;
}
export interface ReportItem {
  id: string;
  category: "CONFIRMED_FACT" | "CAUTION" | "UNVERIFIED";
  content: string;
  fact_id: string | null;
  hold_reason: string | null;
  citations: Citation[];
}
export interface Report {
  id: string;
  check_session_id: string;
  version: number;
  status: "DRAFT" | "APPROVED" | "SUPERSEDED";
  summary_text: string;
  created_at: string;
  approved_at: string | null;
  is_stale: boolean;
  held_items: number;
  items: ReportItem[];
}
/** 백엔드 실행기가 만든 비밀 없는 연결 안내(개발 전용). */
export interface Connection {
  base_url?: string;
  session_id: string;
  scenario?: string;
  pdf_submitted?: boolean;
  contract_copy_possession?: string;
  extraction?: string;
  actual_model?: string;
  team_citation_approval?: string;
  report_id?: string;
  facts_confirmed_at?: string;
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, token: string | null, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "NETWORK", `서버(${API_BASE})에 연결하지 못했어요. 백엔드 --serve가 켜져 있는지 확인해 주세요.`);
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.message ?? (Array.isArray(data?.detail) ? data.detail.map((d: { msg: string }) => d.msg).join(", ") : data?.detail);
    throw new ApiError(res.status, data?.code ?? String(res.status), detail ?? "요청을 처리하지 못했어요.");
  }
  return data as T;
}

export const api = {
  login: (email: string, password: string) =>
    call<{ access_token: string; expires_in: number }>("POST", `${PREFIX}/auth/login`, null, { email, password }),
  facts: (token: string, sessionId: string) =>
    call<SessionFacts>("GET", `${PREFIX}/check-sessions/${sessionId}/facts`, token),
  confirm: (token: string, factId: string, action: ConfirmAction, newValue?: string) =>
    call<{ session_status: SessionStatus }>(
      "POST",
      `${PREFIX}/facts/${factId}/confirmation`,
      token,
      // 모름(ACKNOWLEDGE_UNKNOWN)에는 new_value를 보내지 않는다(생략).
      action === "MODIFY" ? { action, new_value: newValue } : { action },
    ),
  report: (token: string, reportId: string) => call<Report>("GET", `${PREFIX}/reports/${reportId}`, token),
};

export async function readConnection(): Promise<Connection> {
  let res: Response;
  let data: { code?: string; message?: string } | null = null;
  try {
    res = await fetch("/dev/connection.json", { cache: "no-store" });
    data = await res.json();
  } catch {
    throw new ApiError(0, "DEV_SERVER", "화면 서버(npm run dev)와 연결이 잠깐 끊겼어요. 자동으로 다시 시도해요.");
  }
  if (!res.ok) throw new ApiError(res.status, data?.code ?? "NO_CONNECTION", data?.message ?? "연결 정보를 읽지 못했어요.");
  return data as Connection;
}

/** 서버 시각 표시. 서버(SystemClock)가 시간대 없이 UTC 시각을 보내면 UTC로 읽어 한국 시각으로 보여 준다. */
export function fmtTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(ts);
  return new Date(hasZone ? ts : `${ts}Z`).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}
export function sameTime(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const t = (x: string) => Date.parse(/([zZ]|[+-]\d{2}:?\d{2})$/.test(x) ? x : `${x}Z`);
  return t(a) === t(b);
}
