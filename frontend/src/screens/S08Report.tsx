import { useEffect, useRef, useState } from "react";
import { api, ApiError, fmtTime, readConnection, sameTime, type Connection, type Report } from "../api";
import { CATEGORY_LABEL, HOLD_LABEL } from "../labels";

interface Props {
  token: string;
  conn: Connection;
  onBack: () => void;
  onAuthError: () => void;
}

/**
 * S08 점검 보고서(초안). 보고서는 AI 대역(백엔드 실행기)이 만든다. 화면은 시민 API GET /reports/{id}만 읽는다.
 * 보고서 ID는 제품 알림·목록 API가 없어 개발용 connection.json에서 읽는다.
 */
export default function S08Report({ token, conn, onBack, onAuthError }: Props) {
  const [report, setReport] = useState<Report | null>(null);
  const [waiting, setWaiting] = useState("보고서 초안을 기다리는 중…");
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const facts = await api.facts(token, conn.session_id);
        if (facts.session_status !== "FACTS_CONFIRMED" || !facts.facts_confirmed_at) {
          setWaiting("사실 확인이 끝나지 않았어요. 사실 확인 화면에서 모든 항목을 처리해 주세요.");
          setReport(null);
          return;
        }
        const c = await readConnection();
        const sameBasis = sameTime(c.facts_confirmed_at, facts.facts_confirmed_at);
        if (!c.report_id || !sameBasis) {
          setWaiting("현재 확인 상태로 만든 초안을 기다리는 중… (대역이 약 1~2초마다 확인)");
          timer = window.setTimeout(tick, 1500);
          return;
        }
        const r = await api.report(token, c.report_id);
        if (alive.current) {
          setReport(r);
          setError(null);
        }
        return;
      } catch (err) {
        const ae = err as ApiError;
        if (ae.status === 401) return onAuthError();
        setError(`${ae.message}${ae.status ? ` (HTTP ${ae.status} · ${ae.code})` : ""}`);
        // 연결이 잠깐 끊긴 경우(화면 서버 재시작 등)는 3초 뒤 다시 시도한다.
        if (ae.status === 0 && alive.current) timer = window.setTimeout(tick, 3000);
      }
    };
    void tick();
    return () => {
      alive.current = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [token, conn.session_id, onAuthError]);

  if (!report) {
    return (
      <section className="card">
        <span className="sid">S08</span>
        <p>{waiting}</p>
        {error && <div className="alert red">{error}</div>}
        <button className="btn ghost" onClick={onBack}>사실 다시 확인</button>
      </section>
    );
  }

  const by = (c: keyof typeof CATEGORY_LABEL) => report.items.filter((i) => i.category === c);
  const zones: [keyof typeof CATEGORY_LABEL, string][] = [
    ["CONFIRMED_FACT", "green"],
    ["CAUTION", "orange"],
    ["UNVERIFIED", "red"],
  ];

  return (
    <section>
      <div className="head">
        <div>
          <span className="sid">S08</span>
          <h2>점검 보고서 v{report.version}</h2>
          <p className="muted small">
            작성 {fmtTime(report.created_at)} · 상태 <code>{report.status}</code> · 보류 {report.held_items}건 · 법률 판단·환급액 확정이 아니에요
          </p>
        </div>
        <div className="row g8">
          <button className="btn ghost" onClick={onBack}>사실 다시 확인</button>
        </div>
      </div>
      {report.is_stale && (
        <div className="alert red" role="alert">
          <b>사실이 바뀌어 이 초안은 승인할 수 없어요.</b> 새 초안을 기다려 주세요.
        </div>
      )}
      <div className="alert blue">
        초안 · 승인 전. 승인 버튼 위치는 팀 결정 전이라(Q-07) 이 화면에 두지 않았어요. 보고서는 합성 대역이 만든 것으로 실제 모델 생성이 아니에요.
      </div>
      <div className="card">
        <p>{report.summary_text}</p>
      </div>
      <div className="zones">
        {zones.map(([cat, tone]) => (
          <div key={cat} className={`card zone ${tone}`}>
            <div className="row">
              <b>{CATEGORY_LABEL[cat]}</b>
              <span className={`badge ${tone}`}>{by(cat).length}</span>
            </div>
            <ul>
              {by(cat).map((i) => (
                <li key={i.id}>
                  {i.content}
                  {i.hold_reason && (
                    <span className="badge gray" style={{ marginLeft: 6 }}>
                      보류 · {HOLD_LABEL[i.hold_reason] ?? i.hold_reason}
                    </span>
                  )}
                  {i.citations.length > 0 && (
                    <div className="muted small">
                      근거:{" "}
                      {i.citations
                        .map((c) => `${c.source_title ?? "출처"}${c.article_ref ? ` ${c.article_ref}` : ""} (${c.check_result})`)
                        .join(", ")}
                    </div>
                  )}
                </li>
              ))}
              {by(cat).length === 0 && <li className="muted">없음</li>}
            </ul>
          </div>
        ))}
      </div>
      <p className="muted small">보고서 ID <code>{report.id}</code> · 점검 ID <code>{report.check_session_id}</code></p>
    </section>
  );
}
