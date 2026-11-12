# 권리채비 AI 백엔드 인수 실행

2026-11-12. 이형민은 백엔드·API·AI·통합 담당, 김교민은 DB 전담이다.
기준은 work/sup-v0.11의 4d38245bc627f3e93299ad495ada510655815620이며,
LastDance main 36c3286477696c724bb194b1660aa8607229f4f0의 상태 확인·PDF 추출을 함께 보존한다.
이 묶음은 합성 사례를 연결하는 개발용 코드다. 실제 모델·LangGraph·공용 서버·프론트 QA는 별도다.

## 첫 실행

통합 저장소의 루트에서 Windows PowerShell 기준으로 실행한다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
cd backend
..\.venv\Scripts\python.exe -m scripts.run_syn001 --verify --output var/syn001_verify
```

Python 3.11 이상이 필요하며 이번 검증 환경은 Python 3.12.14다.
Linux/macOS에서는 해당 가상환경의 `bin/python`을 사용한다.
검증 모드는 일회용 SQLite와 localhost HTTP 서버를 만들고 종료 시 DB를 없앤다.
`result.json`의 PASS는 해당 합성 실행 범위에만 적용한다. 매번 생성되는 ID는 달라진다.

## 이유림의 화면 연결

```powershell
..\.venv\Scripts\python.exe -m scripts.run_syn001 --serve --port 8000 --output var/syn001_serve
```

서버 주소는 `http://127.0.0.1:8000`, 전체 API 설명은 `/docs`다.
개발 화면 출처는 `http://localhost:5173`와 `http://127.0.0.1:5173`만 허용한다.
다른 PC에서 이 주소에 접근할 수 있다는 뜻은 아니다. 공동 실행은 각 PC에서 같은 묶음을 켜는 방식이다.
이형민이 별도로 공용 개발 환경을 정하면 그 주소·버전·계정 발급 경로를 다시 인계한다.

시험 로그인은 `connection.json`의 `credentials_file`이 가리키는
`var/syn001_private_.../private_credentials.json`에서 확인한다.
이 파일·DB·토큰·비밀키는 공유하거나 Git에 넣지 않는다. 콘솔과 공유 로그에는 비밀을 출력하지 않는다.
`connection.json`은 비밀이 없는 사용자·점검·보고서 ID 안내다.
화면은 준비된 점검 ID를 사용하고, 사용자 확인이 끝나면 대역 작업자가 보고서를 생성한다.
보고서 ID는 `connection.json`에서 인계한다. 제품의 보고서 도착 알림·목록 조회 연결은 이번에 추가하지 않았다.

1. 상황 입력 화면(S04): SYN-001은 필라테스 6개월·60만원·진술 4회다. 이 실행에는 PDF를 제출하지 않았다.
2. 사실 확인 화면(S07): 이용횟수의 대역 최초값 6회를 4회로 수정한다. 나머지 알려진 값은 확인한다.
3. 모름 처리: 계약일·해지 요청일·사본 보유 여부는 `ACKNOWLEDGE_UNKNOWN`으로 처리한다.
   `new_value`는 생략 또는 null이다. 사본 보유 여부 UNKNOWN을 사본 없음으로 바꾸지 않는다.
4. 보고서 화면(S08): 알려진 사실, 미확인 3건, 인용 근거 없음에 따른 보류를 구분한다.
   실제 모델 생성이나 팀 근거 인용 승인으로 표시하지 않는다.

사용자 승인·사건 전환은 화면의 명시적 동작으로 남겨 둔다. serve 대역은 이를 자동 실행하지 않는다.
시민 API 토큰을 AI에 빌려주지 않는다. 대역의 읽기는 동일 프로세스의 인증된 사용자·점검 문맥에서
활성 계정·소유권을 확인하고 기존 유스케이스를 재사용한다. 별도 서비스용 읽기 API는 미구현이다.

## 문희정의 관찰

화면에 실제로 표시된 결과와 API 로그 검토를 구분한다. 코드가 통과했다고 화면 QA를 PASS로 옮기지 않는다.
첫 SYN-001에서 6→4·모름 유지·보류를 확인하고, 수정 전 캡처의 계약서 출처 표시 문제가 재발하는지 관찰한다.
오래된 초안 승인 409는 별도 초기 사례에서 검사한다. 실제 연결 URL·커밋·계정 발급 경로·관찰 시각을 적는다.
실제 모델·프론트 연결·기관 요건·PostgreSQL/RLS·PostgresSaver 파기는 이번 로그로 완료 처리하지 않는다.

## 기존 실행 명령과 전체 API의 구분

`uvicorn app.main:app`은 기존 LastDance의 `/health`와 `/api/contracts/extract-text` 전용이다.
첫 전체 흐름은 위 `scripts.run_syn001` 명령으로 실행한다.
운영 조립 함수 `uvicorn app.main:build_production_app --factory`는 별도 검증된 설정과 DB 준비가 필요하다.
이번에는 운영 DB 초기화·이관, main 변경·푸시·병합을 하지 않는다.

## 시험과 안전 경계

```powershell
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check .
```

시험은 pytest가 만든 새 임시 SQLite만 허용한다. `HJ_TEST_DATABASE_URL` 또는
`HJ_TEST_APP_DATABASE_URL`이 있으면 값을 출력하지 않고 DB 연결 전에 차단한다.
PostgreSQL/RLS 시험은 격리 대상 생성·소유가 증명되는 실행 경로를 준비하기 전 BLOCKED다.
실행기는 HJ_* 환경변수가 설정되어 있으면 시작하지 않는다. 필요하면 별도 개발 터미널을 사용한다.
공용 `purge_tasks` 채택은 미승인이다. 로컬 후보 테이블 생성과 공용 채택은 다르다.
실제 PostgresSaver 버전·스키마·저장→복원→삭제 계약이 확인되지 않았으므로 전체 파기 PASS를 선언하지 않는다.

## 이번 코드 변경

- 모름 값의 값·정규화·문서·페이지·위치를 모두 null로 제한한다.
- 사실 변경 전 세션 상태를 검사하고 보고서·사건 전환 전에 미처리 0건과 알려진 사실 1건 이상을 다시 확인한다.
- 알려진 사건 Snapshot이 있으면 날짜 이벤트 0건에서도 날짜 미확인 상담 초안을 만든다. 임의 날짜를 생성하지 않는다.
- 전부 모름 409, ACK 누락·확정 사실 승격 422, 오래된 초안 409, 승인본·Snapshot 보존은 기존 시험과 함께 확인한다.
- 상담 초안 POST는 기존 `{case_id, version}`을 유지한다. 초안 본문 조회 계약은 후속 작업이며 이번 첫 S04/S07/S08 범위와 구분한다.

LastDance의 python-multipart 0.0.33과 인수 소스의 0.0.32 중 이번에는 인수 소스의 0.0.32 핀을 유지했다.
PDF 추가 의존성 pypdf 6.21.4·pydantic-settings 2.16.2를 명시하고 기존 PDF 시험을 새로 실행한다.
기존 `.github/workflows/backend-ci.yml`은 그대로 보존한다. 로컬 검사와 GitHub Actions 실행 결과는 구분한다.

사람용 실행 결과와 증거 묶음을 함께 확인한다. 원본 제출자의 140 PASS·56 PASS는 이번 실행 수에 합산하지 않는다.
