import type { Connection } from "../api";

/** S04 상황 입력. 이번 회차는 준비된 SYN-001 입력을 그대로 쓰고, 자유입력→모델 추출은 하지 않는다. */
export default function S04Input({ conn, onNext }: { conn: Connection; onNext: () => void }) {
  return (
    <section>
      <div className="head">
        <div>
          <span className="sid">S04</span>
          <h2>상황 입력</h2>
          <p className="muted">이번 연결은 팀이 미리 준비한 합성 점검(SYN-001)을 씁니다.</p>
        </div>
      </div>
      <div className="grid2">
        <div className="card">
          <div className="row">
            <b>준비된 입력</b>
            <span className="badge orange">준비된 입력 · 합성 대역</span>
          </div>
          <p className="scenario">{conn.scenario ?? "SYN-001"}</p>
          <dl className="kv">
            <dt>점검 ID</dt>
            <dd><code>{conn.session_id}</code></dd>
            <dt>계약서 PDF</dt>
            <dd>{conn.pdf_submitted ? "제출함" : "제출하지 않음"}</dd>
            <dt>계약서 사본 보유</dt>
            <dd>{conn.contract_copy_possession === "UNKNOWN" ? "모름 (없음이 아님)" : conn.contract_copy_possession ?? "—"}</dd>
            <dt>사실 추출</dt>
            <dd>{conn.extraction ?? "합성 대역"} · 실제 모델 {conn.actual_model ?? "미연결"}</dd>
          </dl>
        </div>
        <div className="card off-card">
          <div className="row">
            <b>직접 입력</b>
            <span className="badge gray">이번 연결 범위 밖</span>
          </div>
          <textarea disabled placeholder="자유 입력 → 실제 모델 추출은 이번 회차에서 연결하지 않아요." />
          <p className="muted small">PDF 업로드도 이번 SYN-001 연결에는 쓰지 않아요.</p>
        </div>
      </div>
      <div className="bar">
        <span className="muted">다음 화면에서 AI(대역)가 정리한 사실을 한 줄씩 확인해요.</span>
        <button className="btn" onClick={onNext}>
          사실 확인으로
        </button>
      </div>
    </section>
  );
}
