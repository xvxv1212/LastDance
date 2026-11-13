import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * 개발 전용: 백엔드 실행기(run_syn001 --serve)가 만든 connection.json(비밀 없음)을
 * /dev/connection.json 으로만 읽어 준다. 점검 ID와 대역이 만든 보고서 ID를 화면에 넘기기 위한 것이다.
 * - 제품의 보고서 알림·목록 API가 아직 없어서 쓰는 임시 연결이다.
 * - private_credentials.json 등 다른 파일은 절대 읽지 않는다. 비밀 성격의 키는 지운다.
 */
function connectionFile(): Plugin {
  const file = path.resolve(
    here,
    process.env.SYN001_CONNECTION ?? "../backend/var/syn001_serve/connection.json",
  );
  const strip = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(strip);
    if (v && typeof v === "object") {
      return Object.fromEntries(
        Object.entries(v as Record<string, unknown>)
          .filter(([k]) => !/password|token|secret|authorization|credential/i.test(k))
          .map(([k, x]) => [k, strip(x)]),
      );
    }
    return v;
  };
  return {
    name: "syn001-connection",
    configureServer(server) {
      server.middlewares.use("/dev/connection.json", (req, res) => {
        if (req.method !== "GET") {
          res.statusCode = 405;
          res.end();
          return;
        }
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        if (!fs.existsSync(file)) {
          res.statusCode = 404;
          res.end(JSON.stringify({ code: "NO_CONNECTION", message: "백엔드 --serve가 아직 connection.json을 만들지 않았어요." }));
          return;
        }
        try {
          res.end(JSON.stringify(strip(JSON.parse(fs.readFileSync(file, "utf-8")))));
        } catch {
          res.statusCode = 500;
          res.end(JSON.stringify({ code: "BAD_CONNECTION", message: "connection.json을 읽지 못했어요." }));
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), connectionFile()],
  // 백엔드 CORS 허용 출처: http://localhost:5173, http://127.0.0.1:5173
  server: { host: "localhost", port: 5173, strictPort: true },
});
