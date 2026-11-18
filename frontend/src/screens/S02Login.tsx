import { useState, type FormEvent } from "react";
import { api, ApiError, readConnection, type Connection } from "../api";

/** S02 시험계정 로그인. 비밀번호는 저장하지 않고 화면에도 미리 채우지 않는다. */
export default function S02Login({ onReady }: { onReady: (token: string, conn: Connection) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const address = email.trim().replace(/^team([1-9][0-9]*)$/i, "synthetic-$1@example.invalid");
      const res = await api.login(address, password);
      const conn = await readConnection(res.access_token);
      setPassword("");
      onReady(res.access_token, conn);
    } catch (err) {
      const ae = err as ApiError;
      setError(ae.status === 401 ? "아이디 또는 비밀번호가 맞지 않아요." : ae.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="card login-card" onSubmit={submit}>
        <span className="sid">S02</span>
        <h1>시험 계정으로 로그인</h1>
        <p className="muted">
          팀에서 받은 아이디와 숫자 6자리 비밀번호를 입력하세요.
        </p>
        <label>
          시험 계정 아이디
          <input placeholder="team1" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" required />
        </label>
        <label>
          비밀번호
          <input type="password" inputMode="numeric" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" required />
        </label>
        {error && <div className="alert red" role="alert">{error}</div>}
        <button className="btn" disabled={busy}>
          {busy ? "확인 중…" : "로그인"}
        </button>
        <div className="off">
          <span className="badge gray">이번 연결 범위 밖</span> 회원가입 · 간편 로그인 · 비밀번호 찾기
        </div>
      </form>
    </main>
  );
}
