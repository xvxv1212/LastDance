## 팀 시연 로그인

팀원은 HTTPS 시연 화면에서 배정받은 `team1`~`team5`와 숫자 6자리를 입력합니다. 각 계정은 별도 SYN-001 점검과 보고서를 사용합니다. 시연 서버를 새로 시작하면 계정별 비밀번호와 점검이 새로 만들어집니다. 운영자는 `connection.json`의 `credentials_file`이 가리키는 비공개 파일에서 해당 팀원의 `login_id`와 `password`만 개별 전달합니다.

정적 화면을 배포할 때 Caddy의 기존 로그인 전용 handle을 아래 블록으로 교체합니다. 기존 정적 파일 handle은 그대로 둡니다.

```caddyfile
@teamLogin {
    method POST
    path /api/v1/auth/login
}
@teamRead {
    method GET
    path_regexp ^/api/v1/(demo/me|check-sessions/[0-9a-f-]+/facts|reports/[0-9a-f-]+)$
}
@teamConfirm {
    method POST
    path_regexp ^/api/v1/facts/[0-9a-f-]+/confirmation$
}
handle @teamLogin {
    reverse_proxy 127.0.0.1:8000
}
handle @teamRead {
    reverse_proxy 127.0.0.1:8000
}
handle @teamConfirm {
    reverse_proxy 127.0.0.1:8000
}
```

# 프론트엔드 — S04·S07·S08 SYN-001 로컬 연결 (개발용)

React 18 + Vite 6 + TypeScript. 승인된 시안의 화면 ID(S02·S04·S07·S08)와 색을 그대로 쓰는 **최소 앱**입니다.
실제 모델·자유입력 추출·보고서 승인·사건 전환·모바일 QA는 이 범위에 없습니다.

## 팀원 화면 실행 (Windows · VS Code)

VS Code에서 최신 main을 받은 LastDance 폴더를 여세요. 각자 자기 PC에서 Python 3.11과 Node.js 22 LTS 환경으로 실행합니다. 이 사례는 로컬 SQLite와 합성 데이터를 사용하므로 Docker, 공용 DB, 모델 API 키를 준비할 필요가 없습니다.

### 1. 처음 한 번 준비하기

VS Code의 **터미널 → 새 터미널**을 열고 아래 명령을 저장소 루트(backend와 frontend가 함께 보이는 폴더)에서 실행하세요. 이미 루트에 .venv가 있다면 첫 줄을 건너뛰세요.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r backend\requirements-dev.txt
npm.cmd --prefix frontend ci
```

### 2. 두 터미널 켜기

**터미널 1 (백엔드)** — 저장소 루트에서 실행하고 출력에 `"server": "RUNNING"`이 보이면 켜 두세요.

```powershell
cd backend
..\.venv\Scripts\python.exe -X utf8 -m scripts.run_syn001 --serve --port 8000 --output var/syn001_serve
```

**터미널 2 (화면)** — VS Code 터미널의 **+**를 눌러 새 PowerShell을 열고 저장소 루트에서 실행하세요.

```powershell
npm.cmd --prefix frontend run dev
```

브라우저에서 [권리채비 로그인 화면](http://localhost:5173)을 엽니다. 다음번에는 2단계부터 시작하세요.

### 3. 시험 계정으로 보고서까지 확인하기

1. **S02 로그인** — VS Code에서 `backend/var/syn001_serve/connection.json`을 열고 `credentials_file` 경로를 확인하세요. 그 경로는 `backend` 기준입니다. 해당 `private_credentials.json`의 해당 계정 `login_id`와 `password`로 로그인하세요. 이 파일과 로그인 값은 본인 PC에서만 사용하고 Git·채팅·캡처에 올리지 마세요.
2. **S04 상황 입력** — 준비된 필라테스 사례를 확인하고 사실 확인으로 이동하세요.
3. **S07 사실 확인(B안)** — 실제 이용횟수는 **6회 → 4회**로 고치고, 사업자명·계약기간·결제금액은 **맞아요**, 계약일·해지 요청일·계약서 사본 보유 여부는 **모름**을 선택한 뒤 **확인 완료**를 누르세요.
4. **S08 점검 보고서** — **확인된 사실 4건**에 수정한 이용횟수 4가 표시되고, **주의·보류 1건**, **미확인 정보 3건**이면 예시와 일치합니다. 주의·보류는 인용할 근거의 승인이 확인되지 않아 해석을 보류한 결과입니다.

종료할 때는 두 터미널에서 각각 Ctrl+C를 누르세요. 백엔드를 다시 시작하면 새 점검 사례와 계정이 생성되므로 브라우저를 새로고침하고 새 계정으로 로그인하세요.

### 연결이 안 될 때

- 화면이 열리지 않으면 터미널 2에 나온 Local 주소와 포트 5173을 확인하세요. 이미 사용 중이면 본인이 이전에 켠 화면 서버를 종료한 뒤 다시 실행하세요.
- 로그인 화면에 연결 정보가 없다고 나오면 터미널 1의 RUNNING과 `backend/var/syn001_serve/connection.json` 생성을 확인하세요. 로그인 후 점검을 읽지 못하면 인증된 `GET /api/v1/demo/me` 요청의 응답을 확인하세요.
- 비밀번호 오류가 나면 현재 connection.json이 지정한 계정 파일을 다시 확인하세요.

## 연결 범위

| 구분 | 내용 |
| :-- | :-- |
| 실제 API (시민 토큰) | `POST /api/v1/auth/login`, `GET /api/v1/check-sessions/{id}/facts`, `POST /api/v1/facts/{id}/confirmation`, `GET /api/v1/reports/{id}` |
| 대역 | 사실 추출(6회 등)과 보고서 초안 생성은 `run_syn001 --serve`의 합성 대역 |
| 호출하지 않음 | AI 내부 `POST /check-sessions/{id}/facts`·`/reports`, 보고서 승인, 사건 전환 |
| 개발 전용 | 시연 서버의 `GET /api/v1/demo/me`가 로그인한 팀원의 점검·보고서 ID를 전달 |

- 확인 완료 조건은 서버와 같음: 미처리(EXTRACTED) 0건 + 확인·수정 1건 이상. 미처리가 있으면 화면에서 막고 목록을 보여 줌.
- 모름(`ACKNOWLEDGE_UNKNOWN`)은 `new_value`를 보내지 않음. 사본 보유 모름을 ‘없음’으로 바꾸지 않음.
- 토큰은 메모리에만 둠(새로고침 시 다시 로그인).
- API 주소를 바꾸려면 `.env.example`을 `.env.local`로 복사해 `VITE_API_BASE` 수정.
