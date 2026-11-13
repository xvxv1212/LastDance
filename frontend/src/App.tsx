import { useCallback, useState } from "react";
import { API_BASE, type Connection } from "./api";
import S02Login from "./screens/S02Login";
import S04Input from "./screens/S04Input";
import S07Facts from "./screens/S07Facts";
import S08Report from "./screens/S08Report";

export type Screen = "S04" | "S07" | "S08";

export default function App() {
  // 토큰은 메모리에만 둔다(새로고침하면 다시 로그인). localStorage에 저장하지 않는다.
  const [token, setToken] = useState<string | null>(null);
  const [conn, setConn] = useState<Connection | null>(null);
  const [screen, setScreen] = useState<Screen>("S04");

  const logout = useCallback(() => {
    setToken(null);
    setConn(null);
    setScreen("S04");
  }, []);

  return (
    <div className="page">
      <header className="top">
        <b className="brand">권리채비 AI</b>
        <span className="muted">개발 연결 · SYN-001</span>
        <div className="top-badges">
          <span className="badge blue">실제 API {API_BASE.replace(/^https?:\/\//, "")}</span>
          <span className="badge gray">추출: 합성 대역</span>
          <span className="badge gray">모델: 미연결</span>
          {token && (
            <button className="link" onClick={logout}>
              로그아웃
            </button>
          )}
        </div>
      </header>
      {!token || !conn ? (
        <S02Login
          onReady={(t, c) => {
            setToken(t);
            setConn(c);
            setScreen("S04");
          }}
        />
      ) : (
        <main className="main">
          <nav className="steps" aria-label="진행 단계">
            {(["S04", "S07", "S08"] as Screen[]).map((s, i) => (
              <button key={s} className={`step ${screen === s ? "on" : ""}`} onClick={() => setScreen(s)}>
                <span className="num">{i + 1}</span>
                {s === "S04" ? "상황 입력" : s === "S07" ? "사실 확인" : "점검 보고서"}
                <span className="sid">{s}</span>
              </button>
            ))}
          </nav>
          {screen === "S04" && <S04Input conn={conn} onNext={() => setScreen("S07")} />}
          {screen === "S07" && (
            <S07Facts token={token} conn={conn} onDone={() => setScreen("S08")} onAuthError={logout} />
          )}
          {screen === "S08" && (
            <S08Report token={token} conn={conn} onBack={() => setScreen("S07")} onAuthError={logout} />
          )}
        </main>
      )}
      <footer className="foot">
        개발용 화면입니다. 법률 판단·환급액 확정이 아니며, 사용자 확인은 객관적 입증이 아닙니다. 합성 자료만 사용합니다.
      </footer>
    </div>
  );
}
