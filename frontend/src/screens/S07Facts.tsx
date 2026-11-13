import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, fmtTime, type ConfirmAction, type Connection, type Fact, type SessionFacts } from "../api";
import { FACT_LABEL, sourceLabel, STATUS_LABEL } from "../labels";

interface Props {
  token: string;
  conn: Connection;
  onDone: () => void;
  onAuthError: () => void;
}

/**
 * S07 사실 확인·수정.
 * 처리 = 맞아요(CONFIRM) · 고치기(MODIFY) · 모름(ACKNOWLEDGE_UNKNOWN, 미확인 항목만).
 * 다음 단계 조건은 서버와 같다: 미처리(EXTRACTED) 0건 + 확인·수정된 사실 1건 이상 → FACTS_CONFIRMED.
 */
export default function S07Facts({ token, conn, onDone, onAuthError }: Props) {
  const [data, setData] = useState<SessionFacts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<Fact[] | "ALL_UNKNOWN" | null>(null);
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});

  const fail = useCallback(
    (err: unknown) => {
      const ae = err as ApiError;
      if (ae.status === 401) return onAuthError();
      setError(`${ae.message}${ae.status ? ` (HTTP ${ae.status} · ${ae.code})` : ""}`);
    },
    [onAuthError],
  );

  const load = useCallback(async () => {
    try {
      setData(await api.facts(token, conn.session_id));
      setError(null);
    } catch (err) {
      fail(err);
    }
  }, [token, conn.session_id, fail]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (fact: Fact, action: ConfirmAction, value?: string) => {
    setBusy(fact.id);
    setError(null);
    setNotice(null);
    try {
      const res = await api.confirm(token, fact.id, action, value);
      setEditing(null);
      setDraft("");
      setBlocked(null);
      await load();
      setNotice(`${FACT_LABEL[fact.fact_key] ?? fact.fact_key}: ${actionText(action)} · 점검 상태 ${res.session_status}`);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  if (!data) {
    return <section className="card">{error ? <div className="alert red">{error}</div> : "사실 목록을 불러오는 중…"}</section>;
  }

  const facts = data.facts;
  const pending = facts.filter((f) => f.status === "EXTRACTED");
  const known = facts.filter((f) => f.status === "CONFIRMED" || f.status === "MODIFIED");
  const unknown = facts.filter((f) => f.status === "ACKNOWLEDGED_UNKNOWN" || f.status === "REJECTED");
  const ready = data.session_status === "FACTS_CONFIRMED";

  const complete = () => {
    if (pending.length > 0) {
      setBlocked(pending);
      rowRefs.current[pending[0].id]?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (known.length === 0) {
      setBlocked("ALL_UNKNOWN");
      return;
    }
    if (ready) onDone();
    else void load();
  };

  return (
    <section>
      <div className="head">
        <div>
          <span className="sid">S07</span>
          <h2>정리된 사실이 맞는지 확인해 주세요</h2>
          <p className="muted">한 줄씩 ‘맞아요 / 고치기 / 모름’을 골라 주세요. 모름은 사실 확인이 아니라 ‘지금 모른다’는 표시예요.</p>
        </div>
        <div className="counter" aria-live="polite">
          <span className="muted small">처리한 항목</span>
          <b>
            {facts.length - pending.length} / {facts.length}
          </b>
          <span className="muted small">
            확인 {known.length} · 미확인 {unknown.length}
          </span>
        </div>
      </div>

      {blocked && (
        <div className="alert red" role="alert">
          {blocked === "ALL_UNKNOWN" ? (
            <b>모든 항목이 ‘모름’이면 보고서를 만들 수 없어요. 알고 있는 사실을 1개 이상 확인해 주세요.</b>
          ) : (
            <>
              <b>아직 처리하지 않은 항목이 {blocked.length}개 있어요</b>
              <span> — {blocked.map((f) => FACT_LABEL[f.fact_key] ?? f.fact_key).join(" · ")}</span>
            </>
          )}
        </div>
      )}
      {error && <div className="alert red" role="alert">{error}</div>}
      {notice && <div className="alert green" role="status">{notice}</div>}

      <div className="card table-card">
        <table>
          <thead>
            <tr>
              <th>항목</th>
              <th>정리된 값</th>
              <th>출처</th>
              <th>상태</th>
              <th>확인</th>
            </tr>
          </thead>
          <tbody>
            {facts.map((f) => {
              const src = sourceLabel(f.source_kind, f.page_no);
              const st = STATUS_LABEL[f.status];
              const isUnknownSource = f.source_kind === "UNKNOWN";
              const isPending = f.status === "EXTRACTED";
              const hl = blocked && blocked !== "ALL_UNKNOWN" && blocked.some((b) => b.id === f.id);
              return (
                <tr key={f.id} ref={(el) => { rowRefs.current[f.id] = el; }} className={hl ? "hl" : f.status === "MODIFIED" ? "mod" : ""}>
                  <td className="muted">{FACT_LABEL[f.fact_key] ?? f.fact_key}</td>
                  <td>
                    <b>{f.current_value ?? (isUnknownSource ? "—" : f.ai_value ?? "—")}</b>
                    {f.status === "MODIFIED" && f.ai_value && f.ai_value !== f.current_value && (
                      <div className="muted small">
                        처음 값(대역) <s>{f.ai_value}</s> → 사용자가 수정
                      </div>
                    )}
                    {f.fact_key === "OTHER" && f.status === "ACKNOWLEDGED_UNKNOWN" && (
                      <div className="muted small">모름 — ‘사본 없음’으로 바꾸지 않아요</div>
                    )}
                  </td>
                  <td>
                    <span className={`badge ${src.tone}`}>{src.text}</span>
                  </td>
                  <td>
                    <span className={`badge ${st.tone}`}>{st.text}</span>
                  </td>
                  <td className="actions">
                    {editing === f.id ? (
                      <form
                        className="edit"
                        onSubmit={(e) => {
                          e.preventDefault();
                          if (draft.trim()) void act(f, "MODIFY", draft.trim());
                        }}
                      >
                        <input
                          autoFocus
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          aria-label={`${FACT_LABEL[f.fact_key] ?? f.fact_key} 새 값`}
                          placeholder="새 값"
                        />
                        <button className="btn sm" disabled={!draft.trim() || busy === f.id}>저장</button>
                        <button type="button" className="btn sm ghost" onClick={() => setEditing(null)}>취소</button>
                      </form>
                    ) : (
                      <div className="row g4">
                        {isPending && !isUnknownSource && (
                          <button className="btn sm ghost" disabled={!!busy} onClick={() => act(f, "CONFIRM")}>맞아요</button>
                        )}
                        <button
                          className="btn sm ghost"
                          disabled={!!busy}
                          onClick={() => {
                            setEditing(f.id);
                            setDraft(f.current_value ?? "");
                          }}
                        >
                          {isUnknownSource && f.current_value === null ? "값 입력" : isPending ? "고치기" : "다시 고치기"}
                        </button>
                        {isPending && isUnknownSource && (
                          <button className="btn sm ghost" disabled={!!busy} onClick={() => act(f, "ACKNOWLEDGE_UNKNOWN")}>모름</button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="bar">
        <span className="muted small">
          점검 상태 <code>{data.session_status}</code>
          {data.facts_confirmed_at && <> · 확인 시각 {fmtTime(data.facts_confirmed_at)}</>} · 확인 상태와 시각만
          기록돼요(사건 Snapshot 아님).
        </span>
        <button className="btn" onClick={complete}>
          확인 완료
        </button>
      </div>
    </section>
  );
}

function actionText(a: ConfirmAction): string {
  return a === "CONFIRM" ? "확인함" : a === "MODIFY" ? "고침" : a === "ACKNOWLEDGE_UNKNOWN" ? "모름으로 남김" : "틀림 표시";
}
