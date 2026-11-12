# 권리채비 AI · LastDance 팀

헬스장·필라테스 회원권 중도 해지·환불 분쟁 상황에서 사용자가 사실·증거를 정리하고
공식 상담 준비를 할 수 있도록 돕는 웹서비스의 **백엔드 인수용 개발 사본**입니다.
법률 결론, 승소 가능성, 확정 환불액을 자동 판정하지 않습니다.

- 프로젝트 개발 마감: **2026-12-15**
- 상태: **인수 후 상담자료 조회·근거 승인·실제 체크포인트 계약 검증 / 실제 모델·그래프·공용 환경·프론트 연결은 후속**
- 담당: **이형민 백엔드·API·통합 / 김교민 DB 전담**. 채효석 AI 배정은 추천 상태입니다.
- 기준: LastDance main `36c3286`과 인수 소스 `4d38245`의 이력을 보존한 별도 작업 브랜치입니다.

## 현재 첫 실행

**[백엔드 인수 실행 방법](backend/TAKEOVER_RUN.md)**에서 시작합니다.
`python -m scripts.run_syn001 --verify`는 격리 DB와 실제 localhost HTTP로 첫 합성 사례를 검사합니다.
`python -m scripts.run_syn001 --serve --port 8000`은 이유림의 화면 연결을 위한 개발 서버를 유지합니다.
추출은 대역이며 미승인 근거는 보류합니다. 실제 모델·화면 QA·공용 서버 완료를 뜻하지 않습니다.

2026-11-12 사용자가 공용 코드·환경 적용, `purge_tasks` 공용 채택,
원문·내용 해시·버전에 연결된 팀 인용 승인을 모두 승인했습니다.
후속 코드는 별도 브랜치에 있으며, GitHub 연결 앱의 쓰기 요청이 403으로 거부되어
원격 브랜치·PR·main 반영은 아직 완료하지 못했습니다. 승인 자체를 다시 기다리는 상태는 아닙니다.
전체 실행 증거는 동봉 결과 요약과 로그에서 확인합니다.

## 디렉터리

| 경로 | 용도 | 실제 상태 |
| :-- | :-- | :-- |
| `backend/app/api/` | FastAPI 라우터 | PDF API 구현됨 |
| `backend/app/schemas/` | Pydantic 요청·응답 모델 | PDF 응답 모델 구현됨 |
| `backend/app/services/` | 처리 로직 | PDF 추출 구현됨 |
| `backend/app/interface/` | 인수한 시민·내부 API | 시험계정·사실 확인·보고서·사건·상담 API |
| `backend/app/application/` | 인수한 서비스 흐름 | 모름 처리·승인·상담 초안 경계 보완 |
| `backend/app/infrastructure/` | 인수한 DB·파일 연결 | SQLite 및 임시 PostgreSQL 체크포인트 검증, 공용 적용 별도 |
| `backend/app/db/` | 모델·DB 세션 | 폴더만 준비됨 |
| `backend/app/agent/` | LangGraph 상태·노드 | 폴더만 준비됨 |
| `backend/alembic/` | DB 버전 관리 | 미설치·미초기화 (안내만 있음) |
| `backend/tests/` | API 및 PDF 처리 테스트 | 구현됨 |
| `frontend/` | React/Vite/TS 화면 개발 후보 | 앱 미생성 |
| `data/synthetic/` | 개인정보 없는 합성 사례 | 샘플 1건 제공 |
| `docs/` | API/DB 초안 및 개발 시작 안내 | 초안 작성됨 |
| `.github/workflows/` | GitHub PR 자동 검사 | 워크플로우 파일 추가 |

## 기존 상태 확인과 PDF 전용 실행

아래는 기존 진입점입니다. 전체 인수 API는 위의 현재 첫 실행을 사용합니다.
Windows + VS Code 기존 설명은 **[`docs/DEVELOPMENT_START.md`](docs/DEVELOPMENT_START.md)**에 보존합니다.

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
