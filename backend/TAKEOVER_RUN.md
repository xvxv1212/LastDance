# 권리채비 AI 백엔드 인수 실행

2026-11-12. 이형민은 백엔드·API·AI·통합 담당, 김교민은 DB 전담이다.
기준은 work/sup-v0.11의 4d38245bc627f3e93299ad495ada510655815620이며,
LastDance main 36c3286477696c724bb194b1660aa8607229f4f0의 상태 확인·PDF 추출을 함께 보존한다.
이 묶음은 합성 사례를 연결하는 개발용 코드다. 실제 모델·LangGraph·공용 서버·프론트 QA는 별도다.

## 2026년 11월 12일 후속 승인과 작업

사용자는 공용 코드·환경 적용, `purge_tasks` 공용 DB 채택, 원문·내용 해시·버전에 연결된
팀 인용 승인 3건을 모두 승인했다. 아래의 인수 회차에서 미승인으로 남겼던 항목은 이제 승인 완료다.
원격 반영은 변경 브랜치와 PR 검증을 거쳐 수행한다. 현재 연결된 GitHub 앱은 브랜치 생성 요청을
403(Resource not accessible by integration)으로 거부했으므로 원격 코드·PR·main은 변경하지 못했다.
같은 승인을 다시 요청하지 않는다. 인증된 VS Code에서 동봉 Git 번들의 작업 브랜치를 Push한 뒤
PR·CI·Merge를 이어갈 수 있다. 공용·운영 서버의 구체 대상은 아직 확인되지 않았다.

## 첫 실행

통합 저장소의 루트에서 Windows PowerShell 기준으로 실행한다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r backend\requirements-dev.txt
cd backend
..\.venv\Scripts\python.exe -X utf8 -m scripts.run_syn001 --verify --output var/syn001_verify
```

Python 3.11 이상이 필요하며 이번 검증 환경은 Python 3.12.14다.
Linux/macOS에서는 해당 가상환경의 `bin/python`을 사용한다.
검증 모드는 일회용 SQLite와 localhost HTTP 서버를 만들고 종료 시 DB를 없앤다.
`result.json`의 PASS는 해당 합성 실행 범위에만 적용한다. 매번 생성되는 ID는 달라진다.

## 이유림의 화면 연결

```powershell
..\.venv\Scripts\python.exe -X utf8 -m scripts.run_syn001 --serve --port 8000 --output var/syn001_serve
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
인수 회차에서는 운영 DB 초기화·이관과 원격 main 반영을 하지 않았다. 후속 적용 승인은 완료됐으며,
현재 원격 반영의 연결 앱 권한 문제와 실제 운영 대상 미확보를 각각 기록한다.

## 시험과 안전 경계

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest -q -ra
..\.venv\Scripts\python.exe -X utf8 -m ruff check .
```

### Windows에서 확인된 호환성 사항

- `-X utf8`은 한국어 주석과 자료를 UTF-8로 읽도록 해당 Python 프로세스에 적용한다.
- 인용 증거 디렉터리는 `.gitattributes`의 `-text`로 원본 바이트를 보존한다. 승인 기록의
  SHA-256과 실제 파일을 대조하므로 Git이 LF를 CRLF로 바꾸면 인용이 보류된다.
  기존 체크아웃에서 이미 바뀐 파일은 본문 변경 여부를 확인한 뒤 Git 원본 바이트로 복원해야 한다.
  승인 해시를 바꾸거나 검증을 완화해서 통과시키지 않는다.
- PDF는 `.gitattributes`의 `binary`로 줄바꿈 변환을 막는다. PDF의 내부 위치 정보는
  바이트 단위이므로 텍스트 파일처럼 변환하면 읽기 경고가 발생할 수 있다.
- 실제 심볼릭 링크 시험은 Windows의 링크 생성 권한 오류 `WinError 1314`일 때만
  `NOT RUN` 사유와 함께 건너뛴다. 이 항목은 통과로 집계하지 않는다. Linux와 링크 생성이
  가능한 Windows에서는 실제 링크 차단을 검증하며, 기존 파일 거부 시험은 계속 실행한다.
- PostgreSQL 바이너리·pgvector·RLS가 필요한 시험의 `SKIPPED`는 해당 환경에서 미실행을 뜻한다.
  로컬 SQLite 시험 결과를 PostgreSQL/RLS 통과로 보고하지 않는다.

시험은 pytest가 만든 새 임시 SQLite만 허용한다. `HJ_TEST_DATABASE_URL` 또는
`HJ_TEST_APP_DATABASE_URL`이 있으면 값을 출력하지 않고 DB 연결 전에 차단한다.
PostgreSQL/RLS 시험은 격리 대상 생성·소유가 증명되는 실행 경로를 준비하기 전 BLOCKED다.
실행기는 HJ_* 환경변수가 설정되어 있으면 시작하지 않는다. 필요하면 별도 개발 터미널을 사용한다.
공용 `purge_tasks` 채택은 2026-11-12 사용자 승인 완료다. 실제 공용 DB 적용은 별도 실행 결과로 기록한다.
후속 격리 검증은 PostgresSaver 3.2.0과 langgraph-checkpoint 4.3.1의 공식 setup/delete_thread 계약을 사용한다.
이 검증은 임시 PostgreSQL 범위이며 실제 그래프·모델·운영 전체 파기 PASS를 의미하지 않는다.

## 이번 코드 변경

- 모름 값의 값·정규화·문서·페이지·위치를 모두 null로 제한한다.
- 사실 변경 전 세션 상태를 검사하고 보고서·사건 전환 전에 미처리 0건과 알려진 사실 1건 이상을 다시 확인한다.
- 알려진 사건 Snapshot이 있으면 날짜 이벤트 0건에서도 날짜 미확인 상담 초안을 만든다. 임의 날짜를 생성하지 않는다.
- 전부 모름 409, ACK 누락·확정 사실 승격 422, 오래된 초안 409, 승인본·Snapshot 보존은 기존 시험과 함께 확인한다.
- 상담 초안 POST는 기존 `{case_id, version}`을 유지한다. 새 GET `/api/v1/cases/{case_id}/consultation-preps`는
  본인의 최신 저장자료를 읽으며 다른 사용자·삭제 사건·미생성은 동일 404, 비활성·삭제 계정은 401이다.
  `--verify`는 실제 HTTP 응답으로 본문·모름 질문 3개·근거 보류 질문 1개와 날짜 이벤트 0건을 확인한다.
  상담 준비 화면 S13의 실제 연결은 이유림의 후속 작업이며 첫 S04/S07/S08의 선행조건이 아니다.

LastDance의 python-multipart 0.0.33과 인수 소스의 0.0.32 중 이번에는 인수 소스의 0.0.32 핀을 유지했다.
PDF 추가 의존성 pypdf 6.21.4·pydantic-settings 2.16.2를 명시하고 기존 PDF 시험을 새로 실행한다.
기존 `.github/workflows/backend-ci.yml`은 그대로 보존한다. 로컬 검사와 GitHub Actions 실행 결과는 구분한다.

사람용 실행 결과와 증거 묶음을 함께 확인한다. 원본 제출자의 140 PASS·56 PASS는 이번 실행 수에 합산하지 않는다.

## 실제 체크포인트 격리 검증

새 가상환경에서 `python -m pip install -r requirements-checkpoint-test.txt` 후 실행한다.

```powershell
python -m scripts.verify_checkpoint_lifecycle --output var/checkpoint_verify
```

이 명령은 자체 생성한 임시 PostgreSQL 데이터 디렉터리와 loopback 연결만 사용한다.
외부·기존 DB URL을 받아 시험하지 않는다. pgserver 설치나 실행 환경이 없으면 BLOCKED로 보고한다.
`HJ_TEST_DATABASE_URL` 가드를 해제하거나 운영 URL을 넣어 해결하지 않는다.
격리 시험은 저장·복원·대상 thread 삭제·비교 thread 보존·권한 실패 뒤 작업 유지와 재시도를 확인한다.
정확한 통과 수와 환경은 새 로그를 따른다. 김교민은 DB 구조·권한·롤백 영향을 검토한다.

## 원문과 팀 인용 승인 대조

```powershell
python -m scripts.load_knowledge --validate-only
```

이 명령은 DB에 연결하지 않고 원문 발췌·본문 SHA-256·출처·버전·사용자 승인 기록을 대조한다.
기본 승인 파일은 `data/knowledge/approvals.json`이며, 운영 조립은 같은 검증 어댑터를 필수 적용한다.
누락·해시/버전 불일치는 기존 보류 경로로 남긴다. 현재 실제 자료는 한국소비자원의 1372 전화상담
연결 안내 1건이다. 공식 발행 버전은 미표시이며 내부 snapshot 버전과 구분한다.
인용 승인은 문장의 법적 적용이나 확정 환불액을 검증한 결과가 아니다. 기존 SYN-001 대역은
근거가 없는 보류 초안 경로를 그대로 시험한다.
