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
      const conn = await readConnection();
      const res = await api.login(email.trim(), password);
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
          백엔드 <code>--serve</code>가 만든 <code>private_credentials.json</code>의 첫 번째 계정을 입력하세요. 이 값은
          채팅·Git·캡처에 남기지 않습니다.
        </p>
        <label>
          시험 계정 이메일
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" required />
        </label>
        <label>
          비밀번호
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" required />
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
