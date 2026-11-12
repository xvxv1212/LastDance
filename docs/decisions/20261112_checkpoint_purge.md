# 체크포인트 실제 파기와 공용 파기 기록 채택 — 2026-11-12

## 승인과 담당

2026-11-12 사용자가 직전 승인 사항 세 가지를 모두 승인하고 후속 작업 시작을 요청했다.
이에 따라 `purge_tasks`의 공용 DB 구조 채택은 승인 완료다. 이전 인계에서 미승인이었던 사실은
이력으로 보존한다. 승인 자체와 실제 운영 DB에 DDL을 적용한 결과는 구분한다.

이형민은 API·서비스·LangGraph·체크포인터·파기 배치를 구현한다. 김교민은 DDL·ORM·SQL
repository·RLS·이관 및 DB 영향을 검토한다. 이번 변경은 이 담당을 재배정하지 않는다.

## 교체한 계약

기존 `LangGraphCheckpointStore`는 표 세 개 중 하나만 발견해도 직접 `DELETE`를 수행했다.
SQLite에 직접 만든 비슷한 표로 시험했으므로 실제 라이브러리의 버전·전체 스키마·삭제 계약
증거가 아니었다. 이제 아래 버전의 실제 공개 API를 사용한다.

| 항목 | 고정한 기준 |
|---|---|
| PostgreSQL 체크포인터 | `langgraph-checkpoint-postgres==3.2.0` |
| 체크포인트·직렬화 기반 | `langgraph-checkpoint==4.3.1` |
| PostgreSQL 드라이버 | 기존 `psycopg[binary]==3.3.6` 유지 |
| 저장소 준비 | `PostgresSaver.setup()`을 준비 단계에서 명시적으로 호출 |
| 스레드 파기 | `PostgresSaver.delete_thread(thread_id)` |
| migration 계약 | `checkpoint_migrations`의 버전 `0`부터 `9`까지, 누락·초과 없음 |
| 데이터 표 | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` |
| 직렬화 | `JsonPlusSerializer(allowed_msgpack_modules=[])`; 임의 모듈 복원 허용 안 함 |

공식 `setup()`에는 `CREATE INDEX CONCURRENTLY`가 있으므로 연결은 `autocommit=True`,
`row_factory=dict_row`, `prepare_threshold=0`으로 열고 setup 전체를 외부 트랜잭션으로 감싸지
않는다. 서버 시작이나 파기 요청이 임의로 setup을 실행하지 않는다.

삭제 경로는 정확한 패키지 버전, 같은 `current_schema()` 안의 네 표와 필수 열,
전체 migration 이력을 확인한 뒤 실제 `delete_thread`를 호출한다. 삭제·잔존검사는 하나의
트랜잭션으로 감싸 중간 권한 실패가 앞선 삭제만 남기지 않게 한다. 세 데이터 표에 대상
`thread_id`가 남으면 성공으로 처리하지 않는다. 같은 thread의 모든 namespace가 대상이며
다른 thread와 migration 이력은 보존한다.

URL·비밀번호·드라이버 원문 오류는 어댑터 예외에 넣지 않는다. 실패는 기존 배치 계약대로
`purge_tasks`에 `DELETE_FAILED`, 시도 횟수, 다음 재시도 대상을 남긴다. 알려진 스키마를
확인하지 못한 상태를 '미사용' 또는 '파기 완료'로 바꾸지 않는다.

## 실제 검증 환경과 증거 범위

새 로컬 인스턴스만 생성했다. `pgserver==0.1.4`의 PyPI 배포에 포함된 PostgreSQL **16.2**
바이너리를 전용 가상환경에서 사용했다. 이 버전은 이번 합성자료 계약 검증 환경이며 운영
배포 버전을 결정한 것이 아니다. 전체 `langgraph` 실행 엔진·실제 모델은 연결하지 않았다.

환경에서 Unix socket 생성이 거부되어 패키지의 기본 시작 도우미 대신 포함된 `initdb`와
`pg_ctl`을 직접 호출했다. 매번 새 임시 폴더를 만들고, `127.0.0.1` 임의 포트만 열며,
Unix socket은 끈다. 실제 연결의 PGDATA·수신 주소·포트를 확인한 뒤에만 DDL을 실행한다.
끝나면 해당 인스턴스만 종료하고 그 임시 폴더를 제거한다. 기존·외부 DB URL은 입력받지 않는다.

전용 시험은 다음을 실제 PostgreSQL과 실제 PostgresSaver로 확인한다.

1. 공식 setup을 두 번 실행해도 migration 이력이 유지된다.
2. 공식 `put`, `put_writes`, `get_tuple`로 체크포인트·blob·쓰기 기록을 저장하고 읽는다.
3. 두 namespace에 저장한 삭제 대상은 전부 지워지고 다른 thread 및 migration은 남는다.
4. 스키마·migration이 불완전하면 대상이 남고, 명시적 준비 이후에 다시 삭제할 수 있다.
5. 실제 DELETE 권한을 하나 제거하면 `purge_tasks`의 실패 기록이 남고 앞선 삭제도 롤백된다.
   권한 복구 후 다시 실행하면 대상만 삭제되고 대기 행이 사라진다.
6. `app_rw`와 `app_auth`는 파기 기록 및 체크포인트를 열람·수정·삭제하지 못한다.
   이전의 과한 권한도 `db/roles.sql` 재적용으로 회수한다.
7. 외부 DB 환경변수와 검증하지 않은 패키지 버전은 연결 전에 차단한다.

기존 `tests/db_safety.py`와 `tests/conftest.py`의 외부 URL 차단은 변경하지 않았다.
이 전용 시험은 자신이 생성한 DB의 소유를 검증하는 별도 경로다. 기존 PostgreSQL/RLS
전체 회귀의 미실행 상태를 이 시험 결과로 대신하지 않는다.

## 실행

저장소의 `backend` 폴더에서 전용 가상환경을 사용한다.

```bash
python -m pip install -r requirements-checkpoint-test.txt
python -m scripts.verify_checkpoint_lifecycle --output var/checkpoint_verification
python -m pytest -q
```

검증 스크립트는 `summary.json`, `pytest.log`, `pytest.xml`을 저장한다. 출력에는 버전·건수·상태만
남긴다. `pgserver`가 없는 기본 환경에서는 실제 PostgreSQL 시험 다섯 개가 SKIP/BLOCKED이고
환경 차단 세 개와 버전 차단 한 개는 실행된다. 의존성이 있으면 새 로컬 시험 다섯 개도 실행된다.

`HJ_TEST_DATABASE_URL`, `HJ_TEST_APP_DATABASE_URL`, `HJ_DATABASE_URL`,
`HJ_BATCH_DATABASE_URL`, `HJ_AUTH_DATABASE_URL`, `HJ_KNOWLEDGE_DATABASE_URL`,
`HJ_CHECKPOINT_DATABASE_URL` 또는 `PG*` 환경변수가 있으면 격리 검증 시작을 거부한다.
이 값의 운영 여부를 DB 이름으로 추정하지 않는다. 외부 URL을 허용하는 스위치는 없다.

## 공용 DB 반영 순서

1. 현재 대상 DB·스키마·버전·백업·접속 역할을 실제 환경에서 확인한다. 이번 로컬 검증은 이 절차의
   완료 증거가 아니다.
2. 업무 DB가 v0.10이면 기존 `db/migrations/v0.10_to_v0.11.sql`의 이관·복구 조건을 검토해
   적용한다. 이미 v0.11인 DB에 같은 CREATE TABLE을 반복하지 않는다.
3. `db/roles.sql`로 `purge_tasks`의 PUBLIC·시민·인증 역할 권한을 회수하고 배치 역할만 허용한다.
4. 체크포인터 소유자가 `langgraph_checkpoints` 스키마를 만들고 해당 schema를 선택한 연결로
   `LangGraphCheckpointStore(...).setup()`을 명시적으로 실행한다.
5. `db/checkpoint_roles.sql`로 `app_batch`에 schema 사용·migration 조회·데이터 조회/삭제만
   부여한다. 서비스의 `HJ_CHECKPOINT_DATABASE_URL`도 같은 schema를 선택해야 한다.
   라이브러리 표에 직접 작성한 DDL을 덮어쓰지 않는다.
6. 실제 환경의 제한된 합성 thread로 보존·대상 삭제·권한·재시도 증거를 남긴다.

## 아직 완료하지 않은 것

공용 서버 배포, 운영 DB 적용, 실제 Graph 실행 중의 파기 경합, 실제 모델, 운영 백업과 외부
저장소의 전체 파기는 이번 검증 범위 밖이다. Graph가 삭제 이후 같은 thread를 다시 쓰지 않도록
실제 실행 중지·재개 계약을 연결한 뒤 검증해야 한다. 이 결과를 '서비스 전체 파기 PASS'로 보고하지
않는다. 실제 Graph 상태에 들어갈 사용자 정의 타입의 직렬화도 별도 연결 시험이 필요하다.

## 확인한 공식 자료

- [PostgresSaver 3.2.0 공식 배포](https://pypi.org/project/langgraph-checkpoint-postgres/3.2.0/)
- [PostgresSaver setup 공식 API](https://reference.langchain.com/python/langgraph.checkpoint.postgres/PostgresSaver/setup)
- [PostgresSaver delete_thread 공식 API](https://reference.langchain.com/python/langgraph.checkpoint.postgres/PostgresSaver/delete_thread)
- [공식 소스 저장소](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres)
- [로컬 검증용 pgserver 배포](https://pypi.org/project/pgserver/0.1.4/)
- [pgserver 소스 저장소](https://github.com/orm011/pgserver)

공식 문서와 별도로 실제 설치된 3.2.0 소스의 setup/delete_thread·migration 목록을 읽고,
위 통합 시험을 실행했다. 단순 문서 조회를 실행 완료로 계산하지 않았다.
