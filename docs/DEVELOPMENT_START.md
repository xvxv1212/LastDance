# 로컬 개발 시작 안내 (Windows + VS Code 기준)

## 0. 이 ZIP에는 무엇이 있나요?
- 기존 Claude ZIP의 `backend`, `data`, `docs`, `.env.example`, `docker-compose.yml` 등을 유지했습니다.
- PDF 추출 기능을 `api` / `schemas` / `services`로 나누었고 응답 형태와 경로는 유지했습니다.
- DB/Agent/프론트는 **폴더 및 설계 초안만 추가**했습니다. 구현된 서비스가 아닙니다.
- `.github/workflows/backend-ci.yml`은 GitHub PR 검사 제안이며 로컬 검증과 별개입니다.

## 1. 기존 GitHub 프로젝트에 적용하기
1. VS Code에서 기존 프로젝트를 열고 **Source Control**에서 변경 파일이 없는지 확인합니다.
2. `main` 최신 상태를 가져온 뒤 `feature/scaffold-v2` 같은 **새 브랜치**를 만듭니다.
3. ZIP을 압축 해제한 `LastDance` 폴더 **안의 내용**을 기존 프로젝트 루트로 복사합니다.
   `기존프로젝트/LastDance/backend`가 되는 중첩 복사는 피하세요.
4. 덮어쓰기 전에 로컬의 수정 파일이 없는지 비교합니다. `.env`와 개인 계약서 원본은 복사·커밋하지 마세요.
5. VS Code 소스 제어 창에서 변경사항을 확인하고 테스트 후 Commit → Push → PR을 진행합니다.

## 2. Windows 첫 실행 (PowerShell)
- 루트 폴더에서 `.env.example`을 `.env`로 복사하고 DB 비밀번호를 설정합니다.
- Docker Desktop 실행 후, 루트 폴더에서 `docker compose up -d`.
- VS Code 터미널에서 `cd backend` 이후 아래 명령을 실행합니다.

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m uvicorn app.main:app --reload
```

- `http://localhost:8000/health`와 `http://localhost:8000/docs`를 브라우저에서 엽니다.
- `GET /health`는 서버 점검일 뿐 DB 연결 점검은 아닙니다.
- Python 3.11 명령이 없다면 설치된 지원 버전의 Python으로 가상환경을 만드세요.
- PowerShell의 실행 정책이 막히면 VS Code에서 `.venv/Scripts/python.exe`를 Python 인터프리터로 선택해도 됩니다.
- 실행 전 모든 의존성 신규 설치·Windows 환경에서의 테스트는 팀에서 별도로 확인해야 합니다.

## 3. 개발 책임의 경계
- 데이터 공동 조사: 공식 자료·출처 및 적용 시점 관리
- DB 담당: `backend/app/db`, `backend/alembic`, `docs/DB_SCHEMA_DRAFT.md` 검토
- 화면 담당(구현자 미확정): `frontend`와 `docs/API_CONTRACT_DRAFT.md` 협의
- 백엔드/AI Agent 구현 담당: 팀 협의 후 확정. 비어 있는 구조를 완료로 표시하지 마세요.
