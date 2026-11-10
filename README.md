# 권리채비 AI · LastDance 팀

헬스장·필라테스 회원권 중도 해지·환불 분쟁 상황에서 사용자가 사실·증거를 정리하고
공식 상담 준비를 할 수 있도록 돕는 AI Agent 기반 웹서비스의 **개발 초기 구조**입니다.
법률 결론, 승소 가능성, 확정 환불액을 자동 판정하지 않습니다.

- 프로젝트 개발 마감: **2026-12-15**
- 상태: **백엔드 기초 PDF 텍스트 추출 구현 / DB·Agent·프론트엔드 미구현**
- 기존 Claude 초안을 바탕으로 구조를 정리한 버전. 팀의 API·DB·화면 계약은 아직 승인 전입니다.

## 디렉터리

| 경로 | 용도 | 실제 상태 |
| :-- | :-- | :-- |
| `backend/app/api/` | FastAPI 라우터 | PDF API 구현됨 |
| `backend/app/schemas/` | Pydantic 요청·응답 모델 | PDF 응답 모델 구현됨 |
| `backend/app/services/` | 처리 로직 | PDF 추출 구현됨 |
| `backend/app/db/` | 모델·DB 세션 | 폴더만 준비됨 |
| `backend/app/agent/` | LangGraph 상태·노드 | 폴더만 준비됨 |
| `backend/alembic/` | DB 버전 관리 | 미설치·미초기화 (안내만 있음) |
| `backend/tests/` | API 및 PDF 처리 테스트 | 구현됨 |
| `frontend/` | React/Vite/TS 화면 개발 후보 | 앱 미생성 |
| `data/synthetic/` | 개인정보 없는 합성 사례 | 샘플 1건 제공 |
| `docs/` | API/DB 초안 및 개발 시작 안내 | 초안 작성됨 |
| `.github/workflows/` | GitHub PR 자동 검사 | 워크플로우 파일 추가 |

## 시작 방법

Windows + VS Code 기준 단계별 설명은 **[`docs/DEVELOPMENT_START.md`](docs/DEVELOPMENT_START.md)**를 읽으세요.

```powershell
# 저장소 루트에서
Copy-Item .env.example .env
# .env 에 POSTGRES_PASSWORD와 DATABASE_URL 비밀번호 입력
docker compose up -d
cd backend
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m uvicorn app.main:app --reload
```

`http://localhost:8000/health` -> `{"status":"ok"}`이면 API 서버가 실행된 것입니다.
`http://localhost:8000/docs`에서 실제 구현된 API를 확인할 수 있습니다.
DB 연결·Agent 작동·프론트엔드가 준비되었다는 뜻은 아닙니다.

## 개발 전 합의할 문서

- [`docs/API_CONTRACT_DRAFT.md`](docs/API_CONTRACT_DRAFT.md) — 현재 API와 이후 후보 API 구분
- [`docs/DB_SCHEMA_DRAFT.md`](docs/DB_SCHEMA_DRAFT.md) — 사용자 확인·승인 데이터 저장 초안
- [`docs/decisions/`](docs/decisions/) — 팀 승인 결정 기록 보관

## 협업·안전 규칙

1. 최신 `main`에서 기능 브랜치를 만들고 Commit → Push → PR → 리뷰 → Merge 순서로 작업합니다.
2. `main` 직접 수정, `force push`, 무분별한 `reset --hard`를 피합니다.
3. `.env`, API 키, 사용자 계약서 원본, 개인정보를 Git에 올리지 않습니다.
4. AI 추출 정보는 **미확인 사실 후보**이고 사용자 승인 전 최종 근거로 취급하지 않습니다.
5. 법률·공공기관 자료는 출처·시행일·검증 상태를 기록합니다.
6. 미구현 기능과 제안된 인터페이스를 '구현 완료'라고 표시하지 않습니다.
