import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { api, ApiError, fmtTime, type ConfirmAction, type Connection, type Fact, type SessionFacts } from "../api";
import { FACT_LABEL, factQuestion, sourceLabel, STATUS_LABEL } from "../labels";

interface Props {
  token: string;
  conn: Connection;
  onDone: () => void;
  onAuthError: () => void;
}

/**
 * S07 사실 확인 — B안 배치(질문 하나 + 전체 확인 항목).
 * 처리 = 네, 맞아요(CONFIRM) · 수정할게요(MODIFY) · 모르겠어요(ACKNOWLEDGE_UNKNOWN, 출처 UNKNOWN 항목만).
 * 질문 수·값·출처는 서버 facts에서 계산한다(고정 숫자·시연 데이터 없음).
 * 다음 단계 조건은 서버와 같다: 미처리(EXTRACTED) 0건 + 확인·수정된 사실 1건 이상 → FACTS_CONFIRMED.
 */
export default function S07Facts({ token, conn, onDone, onAuthError }: Props) {
  const [data, setData] = useState<SessionFacts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null); // 지금 보고 있는 fact id(null = 다음 미처리 자동)
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState<Fact[] | "ALL_UNKNOWN" | null>(null);
  const questionRef = useRef<HTMLHeadingElement | null>(null);

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
      const d = await api.facts(token, conn.session_id);
      setData(d);
      setError(null);
      return d;
    } catch (err) {
      fail(err);
      return null;
    }
  }, [token, conn.session_id, fail]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    questionRef.current?.focus();
  }, [cursor]);

  if (!data) {
    return (
      <section className="b07">
        <div className="b-surface">{error ? <div className="b-error" role="alert">{error}</div> : "사실 목록을 불러오는 중…"}</div>
      </section>
    );
  }

  const facts = data.facts;
  const n = facts.length;
  const pending = facts.filter((f) => f.status === "EXTRACTED");
  const known = facts.filter((f) => f.status === "CONFIRMED" || f.status === "MODIFIED");
  const unknown = facts.filter((f) => f.status === "ACKNOWLEDGED_UNKNOWN" || f.status === "REJECTED");
  const done = n - pending.length;
  const label = (f: Fact) => FACT_LABEL[f.fact_key] ?? f.fact_key;

  // 지금 보여 줄 질문: 고른 항목 → 없으면 첫 미처리 → 모두 처리했으면 완료 안내
  const current = facts.find((f) => f.id === cursor) ?? (cursor === null ? pending[0] : undefined) ?? null;
  const idx = current ? facts.findIndex((f) => f.id === current.id) : -1;

  const nextAfter = (d: SessionFacts | null, fromId: string): string | null => {
    if (!d) return null;
    const list = d.facts;
    const start = list.findIndex((f) => f.id === fromId);
    for (let k = 1; k <= list.length; k++) {
      const f = list[(start + k) % list.length];
      if (f.status === "EXTRACTED") return f.id;
    }
    return null;
  };

  const act = async (fact: Fact, action: ConfirmAction, value?: string) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.confirm(token, fact.id, action, value);
      setEditing(false);
      setDraft("");
      setEditError(null);
      setBlocked(null);
      const d = await load();
      setCursor(nextAfter(d, fact.id) ?? "__ready");
      setNotice(`${label(fact)}: ${actionText(action)} · 점검 상태 ${res.session_status}`);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const open = (id: string) => {
    setCursor(id);
    setEditing(false);
    setEditError(null);
  };

  const startEdit = (f: Fact) => {
    setEditing(true);
    setEditError(null);
    setDraft(f.current_value ?? "");
  };

  const saveEdit = (f: Fact) => {
    const v = draft.trim();
    if (!v) {
      setEditError("내용을 입력하거나, 취소한 뒤 다른 답을 골라 주세요.");
      return;
    }
    void act(f, "MODIFY", v);
  };

  const complete = () => {
    if (pending.length > 0) {
      setBlocked(pending);
      open(pending[0].id);
      return;
    }
    if (known.length === 0) {
      setBlocked("ALL_UNKNOWN");
      return;
    }
    if (data.session_status === "FACTS_CONFIRMED") onDone();
    else void load();
  };

  const isBlockedFact = (f: Fact) => blocked !== null && blocked !== "ALL_UNKNOWN" && blocked.some((b) => b.id === f.id);

  return (
    <section className="b07">
      <div className="b-eyebrow">
        <span className="sid">S07</span> 사실 확인
      </div>
      <h1 className="b-title">정리한 내용이 맞는지, 하나씩 볼까요?</h1>
      <p className="b-lead">잘못 읽은 내용은 고치고, 모르는 내용은 그대로 남겨 주세요.</p>

      {blocked && (
        <div className="b-alert" role="alert">
          {blocked === "ALL_UNKNOWN" ? (
            <b>모든 항목이 ‘모름’이면 보고서를 만들 수 없어요. 알고 있는 사실을 1개 이상 확인해 주세요.</b>
          ) : (
            <>
              <b>아직 처리하지 않은 항목이 {blocked.length}개 있어요</b>
              <span> — {blocked.map(label).join(" · ")}</span>
            </>
          )}
        </div>
      )}
      {error && <div className="b-alert" role="alert">{error}</div>}
      {notice && (
        <div className="b-notice" role="status">
          {notice}
        </div>
      )}

      <div className="b-layout">
        {current ? (
          <QuestionCard
            key={current.id}
            f={current}
            idx={idx}
            n={n}
            done={done}
            editing={editing}
            draft={draft}
            editError={editError}
            busy={busy}
            questionRef={questionRef}
            onDraft={setDraft}
            onConfirm={() => act(current, "CONFIRM")}
            onUnknown={() => act(current, "ACKNOWLEDGE_UNKNOWN")}
            onEdit={() => startEdit(current)}
            onSave={() => saveEdit(current)}
            onCancel={() => {
              setEditing(false);
              setEditError(null);
            }}
            sessionStatus={data.session_status}
            confirmedAt={data.facts_confirmed_at}
          />
        ) : (
          <section className="b-surface" aria-labelledby="b-ready-title">
            <h2 className="b-ready-title" id="b-ready-title" tabIndex={-1} ref={questionRef}>
              {pending.length ? "다음 항목을 골라 주세요" : "확인할 항목을 모두 살펴봤어요."}
            </h2>
            <p>
              내가 확인한 내용 <strong>{known.length}개</strong> · 모름으로 남긴 내용 <strong>{unknown.length}개</strong>
              {pending.length > 0 && (
                <>
                  {" "}
                  · 아직 확인 전 <strong>{pending.length}개</strong>
                </>
              )}
            </p>
            <div className="b-notice">
              모르는 내용은 보고서에도 <b>‘미확인’</b>으로 남습니다. 아직 사건 기록으로 저장한 것은 아닙니다.
            </div>
            <button className="b-primary b-full" onClick={complete}>
              확인 완료하고 점검 결과 보기 →
            </button>
            <button className="b-text-btn" onClick={() => open(facts[0].id)}>
              처음 항목부터 다시 보기
            </button>
            <p className="b-status-note">
              점검 상태 <code>{data.session_status}</code>
              {data.facts_confirmed_at && <> · 확인 시각 {fmtTime(data.facts_confirmed_at)}</>} · 확인 상태와 시각만
              기록돼요(사건 Snapshot 아님).
            </p>
          </section>
        )}

        <aside className="b-rail" aria-label="전체 확인 항목">
          <div className="b-rail-head">
            <h2>전체 확인 항목</h2>
            <span className="b-small" data-testid="rail-count">
              {done} / {n} 처리
            </span>
          </div>
          <ul className="b-fact-list">
            {facts.map((f, i) => {
              const st = STATUS_LABEL[f.status];
              const val = f.status === "ACKNOWLEDGED_UNKNOWN" ? "미확인" : f.current_value ?? (f.source_kind === "UNKNOWN" ? "아직 모름" : f.ai_value ?? "—");
              const cls = ["b-fact-link", current?.id === f.id ? "current" : "", isBlockedFact(f) ? "blocked" : ""].filter(Boolean).join(" ");
              return (
                <li key={f.id}>
                  <button className={cls} data-fid={f.id} data-status={st.text} aria-label={`${label(f)} 다시 보기`} onClick={() => open(f.id)}>
                    <span className="b-fact-meta">
                      <span>
                        {i + 1}. {label(f)}
                      </span>
                      <span>{f.status === "EXTRACTED" ? "확인 전" : st.text}</span>
                    </span>
                    <strong>{val}</strong>
                  </button>
                </li>
              );
            })}
          </ul>
          <p className="b-fine">‘모름’도 하나의 응답입니다. 확인된 사실로 바꾸지 않고 미확인으로 남겨요.</p>
          <button className="b-secondary b-full" onClick={complete}>
            확인 완료
          </button>
          <details className="b-detail">
            <summary>내용의 출처는 어떻게 다르죠?</summary>
            <div className="b-inside">
              <p>
                <b>사용자 진술</b>은 직접 설명한 내용, <b>계약서 원문</b>은 올린 계약서에서 찾은 내용, <b>미확인</b>은 아직 알 수
                없는 내용이에요. 고친 값은 ‘고쳐서 확인’으로 표시해요.
              </p>
              <p>계약서를 내지 않은 경우 원문·페이지를 표시하지 않아요.</p>
            </div>
          </details>
        </aside>
      </div>
    </section>
  );
}

interface CardProps {
  f: Fact;
  idx: number;
  n: number;
  done: number;
  editing: boolean;
  draft: string;
  editError: string | null;
  busy: boolean;
  questionRef: MutableRefObject<HTMLHeadingElement | null>;
  onDraft: (v: string) => void;
  onConfirm: () => void;
  onUnknown: () => void;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  sessionStatus: string;
  confirmedAt: string | null;
}

function QuestionCard(p: CardProps) {
  const { f } = p;
  const lab = FACT_LABEL[f.fact_key] ?? f.fact_key;
  const isUnknownSource = f.source_kind === "UNKNOWN";
  const isPending = f.status === "EXTRACTED";
  const value = f.status === "ACKNOWLEDGED_UNKNOWN" ? null : f.current_value ?? (isUnknownSource ? null : f.ai_value);
  const src = sourceLabel(f.source_kind, f.page_no);
  const st = STATUS_LABEL[f.status];
  const changed = f.status === "MODIFIED" && f.ai_value && f.ai_value !== f.current_value;

  return (
    <section className="b-surface" data-fid={f.id}>
      <div className="b-question-count">
        <span>
          질문 {p.idx + 1} / {p.n}
        </span>
        <strong>{p.done}개 처리했어요</strong>
      </div>
      <div className="b-bar" aria-hidden="true">
        <span style={{ width: `${p.n ? (p.done / p.n) * 100 : 0}%` }} />
      </div>
      <h2 className="b-question" tabIndex={-1} ref={p.questionRef}>
        {factQuestion(f.fact_key, lab, isUnknownSource)}
      </h2>

      {value ? (
        <>
          <div className="b-answer">{value}</div>
          <p className="b-origin">
            <span className={`b-tag ${src.tone === "red" ? "" : "accent"}`}>{src.text}</span>
            {!isPending && <span className="b-tag state">{st.text}</span>}
            {changed && <span className="b-was"> 처음 값(대역): {f.ai_value}</span>}
          </p>
        </>
      ) : (
        <>
          <p className="b-empty-hint">
            {f.status === "ACKNOWLEDGED_UNKNOWN" ? "‘모름’으로 남겼어요. 보고서에 미확인으로 표시돼요." : "아직 확인할 수 있는 내용이 없어요."}
            <br />
            모르면 지금 답하지 않아도 괜찮습니다.
          </p>
          <p className="b-origin">
            <span className="b-tag">{src.text}</span>
            {!isPending && <span className="b-tag state">{st.text}</span>}
          </p>
          <div className="b-notice neutral">
            {f.fact_key === "OTHER"
              ? "‘모르겠어요’는 ‘사본 없음’이 아니에요. 계약서를 갖고 있는지 확인되면 입력해 주세요."
              : "기억나는 내용만 적거나 ‘모르겠어요’를 선택해 주세요. 예시 값을 자동으로 채우지 않습니다."}
          </div>
        </>
      )}

      {p.editing ? (
        <div className="b-edit-box">
          <label htmlFor="b-edit-value">{lab} 수정</label>
          <input
            id="b-edit-value"
            className="b-field"
            value={p.draft}
            maxLength={120}
            autoComplete="off"
            autoFocus
            aria-label={`${lab} 새 값`}
            placeholder="아는 내용만 입력해 주세요"
            onChange={(e) => p.onDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                p.onSave();
              }
            }}
          />
          {p.editError && (
            <p className="b-error" role="alert">
              {p.editError}
            </p>
          )}
          <div className="b-actions">
            <button className="b-primary" disabled={p.busy} onClick={p.onSave}>
              이 내용으로 확인
            </button>
            <button className="b-secondary" onClick={p.onCancel}>
              취소
            </button>
          </div>
          <p className="b-fine">고친 값은 ‘고쳐서 확인’으로 표시하고, 처음 값은 기록에 남겨요.</p>
        </div>
      ) : isPending ? (
        isUnknownSource ? (
          <div className="b-question-actions">
            <button className="b-primary" disabled={p.busy} onClick={p.onEdit}>
              아는 내용 입력
            </button>
            <button className="b-secondary" disabled={p.busy} onClick={p.onUnknown}>
              모르겠어요
            </button>
          </div>
        ) : (
          <div className="b-question-actions">
            <button className="b-primary" disabled={p.busy} onClick={p.onConfirm}>
              네, 맞아요
            </button>
            <button className="b-secondary" disabled={p.busy} onClick={p.onEdit}>
              수정할게요
            </button>
          </div>
        )
      ) : (
        <div className="b-question-actions single">
          <button className="b-secondary" disabled={p.busy} onClick={p.onEdit}>
            {isUnknownSource && f.current_value === null ? "아는 내용 입력" : "다시 수정할게요"}
          </button>
        </div>
      )}

      <div className="b-status-note">
        확인했다고 해서 법률상 사실이 입증된 것은 아닙니다.
        <br />
        점검 상태 <code>{p.sessionStatus}</code>
        {p.confirmedAt && <> · 확인 시각 {fmtTime(p.confirmedAt)}</>} · 확인 상태와 시각만 기록돼요(사건 Snapshot 아님).
      </div>
    </section>
  );
}

function actionText(a: ConfirmAction): string {
  return a === "CONFIRM" ? "확인함" : a === "MODIFY" ? "고쳐서 확인" : a === "ACKNOWLEDGE_UNKNOWN" ? "모름으로 남김" : "틀림 표시";
}
