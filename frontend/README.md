# 프론트엔드 — S04·S07·S08 SYN-001 로컬 연결 (개발용)

React 18 + Vite 6 + TypeScript. 승인된 시안의 화면 ID(S02·S04·S07·S08)와 색을 그대로 쓰는 **최소 앱**입니다.
실제 모델·자유입력 추출·보고서 승인·사건 전환·모바일 QA는 이 범위에 없습니다.

## 실행 (Windows PowerShell, 저장소 루트 기준)

터미널 1 — 백엔드 (backend/TAKEOVER_RUN.md와 같음)

```powershell
cd backend
..\.venv\Scripts\python.exe -X utf8 -m scripts.run_syn001 --serve --port 8000 --output var/syn001_serve
```

터미널 2 — 화면

```powershell
cd frontend
npm install        # 처음 한 번. package-lock.json이 생기면 함께 커밋
npm run dev        # http://localhost:5173
```

1. `http://localhost:5173` 접속 → S02에서 `backend/var/syn001_private_*/private_credentials.json`의 **첫 번째 계정**으로 로그인
   (이 값은 Git·채팅·캡처에 남기지 않음)
2. S04: 준비된 SYN-001 입력 확인 (PDF 미제출, 사본 보유 = 모름)
3. S07: 실제 이용횟수 6회 → **고치기** 4회, 사업자명·계약기간·결제금액 **맞아요**, 계약일·해지 요청일·사본 보유 **모름**
4. **확인 완료** → S08: 확인된 사실 4 · 주의·보류 1(NO_CITATION) · 미확인 3

## 연결 범위

| 구분 | 내용 |
| :-- | :-- |
| 실제 API (시민 토큰) | `POST /api/v1/auth/login`, `GET /api/v1/check-sessions/{id}/facts`, `POST /api/v1/facts/{id}/confirmation`, `GET /api/v1/reports/{id}` |
| 대역 | 사실 추출(6회 등)과 보고서 초안 생성은 `run_syn001 --serve`의 합성 대역 |
| 호출하지 않음 | AI 내부 `POST /check-sessions/{id}/facts`·`/reports`, 보고서 승인, 사건 전환 |
| 개발 전용 | 점검 ID·보고서 ID는 Vite가 `backend/var/syn001_serve/connection.json`(비밀 없음)을 `/dev/connection.json`으로 읽어 전달. 제품 알림·목록 API 미구현 때문 |

- 확인 완료 조건은 서버와 같음: 미처리(EXTRACTED) 0건 + 확인·수정 1건 이상. 미처리가 있으면 화면에서 막고 목록을 보여 줌.
- 모름(`ACKNOWLEDGE_UNKNOWN`)은 `new_value`를 보내지 않음. 사본 보유 모름을 ‘없음’으로 바꾸지 않음.
- 토큰은 메모리에만 둠(새로고침 시 다시 로그인).
- API 주소를 바꾸려면 `.env.example`을 `.env.local`로 복사해 `VITE_API_BASE` 수정.
