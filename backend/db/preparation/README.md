# 권리채비 AI DB 설치 준비

2026-11-12 사용자 결정에 따라 서버는 이형민이 따로 선택한다. 지금은 특정 업체에 종속되지 않는 DB 구조·계정 권한·설치 순서·점검 자료를 준비한다. 실제 공용 DB의 주소·버전·접속 계정은 미정이다.

이 문서는 김교민의 DB 전담 업무를 위한 실행 안내다. API·인증 서비스·실제 LangGraph 실행·배포는 이형민이 맡는다. 기존 `purge_tasks` 채택과 공용 반영 승인은 유지하며, 실제 적용 기록과 승인 상태를 구분한다.

## 기준 파일과 이번 변경

- DB 기준 커밋: `fe454f2c18d35b1798bfd92b1d8997fb6c4a57b6`.
- 준비 브랜치: `work/db-prepare-20261112`. 변경 브랜치 → 커밋·푸시 → PR → 검토·병합 경로를 유지한다.
- `schema.sql`, `rls_policies.sql`, `knowledge_schema.sql`, `roles.sql`, `checkpoint_roles.sql`, `table_spec.json`은 기존 파일을 그대로 사용한다.
- 이 폴더의 `00_preflight.sql`과 `90_inspect_installed.sql`은 **조회만 하는 파일**이다. 비밀번호와 업무 행을 읽거나 구조·데이터·권한을 변경하지 않는다.
- 이전 `work/cloud-staging-20261112`의 업체별 배포안은 보류된 참고 자료다. 이 브랜치에 서버 설정을 합치지 않는다.

## 1 설치 전에 확인할 사항

`00_preflight.sql`은 선택된 실제 DB에서 설치 담당자가 실행한다. 현재 대상이 없으므로 실제 대상 결과는 NOT RUN이다. 로컬 시험 결과를 대신 붙이지 않는다.

| 확인 항목 | 다음 단계로 진행할 조건 |
| --- | --- |
| 접속 대상 | DB 이름·버전·설치 계정이 의도한 새 전용 DB와 일치한다. 읽기 전용·복제 대기 서버가 아니다. |
| 생성 위치 | `public`을 사용하며 설치 계정의 USAGE·CREATE가 가능하다. 실행 예제에서 `search_path=public`을 명시한다. |
| 지식 검색 확장 | `vector` 제공 여부와 현재 DB 설치 여부·버전·스키마를 각각 확인한다. 설치 권한과 실제 저장·검색은 별도 검증한다. |
| 역할 생성 권한 | 역할 생성은 CREATEROLE 또는 superuser, BYPASSRLS 지정은 superuser 또는 이미 BYPASSRLS를 가진 역할이 필요하다. 속성상 후보 조건은 `rolsuper OR (rolcreaterole AND rolbypassrls)`이며 실제 환경의 추가 제한을 확인한다. |
| 기존 객체 | 동명 표·스키마·함수·역할과 역할 소속 관계를 대조한다. 역할은 클러스터 공통이므로 새 DB에도 이미 있을 수 있다. 충돌을 자동 삭제·수정하지 않는다. |
| 접속 보안 | 실제 환경의 인증 방식·TLS 검증·네트워크 접근 범위를 정한 뒤 역할별 연결을 확인한다. 로컬 시험의 trust·sslmode=disable 설정을 복사하지 않는다. |

`vector`가 이미 `public` 밖에 설치되어 있으면 원본 SQL의 `embedding vector` 형식이 해결되는지 먼저 검토한다. 임의로 확장을 이동하거나 삭제하지 않는다. 기존 역할은 `roles.sql`이 속성과 모든 과잉 권한을 자동 교정하지 않는다.

## 2 새 빈 DB 설치 순서

| 순서 | 파일 또는 작업 | 확인할 결과 |
| --- | --- | --- |
| 1 | `db/schema.sql` | public 업무 표 11개와 제약·트리거 |
| 2 | `db/rls_policies.sql` | 개인정보 표 9개에 행별 접근 제한 및 강제 적용 |
| 3 | `db/knowledge_schema.sql` | vector 확장, knowledge.sources·chunks 2개 표 |
| 4 | `db/roles.sql` | 앱·로그인·파기·지식 적재용 역할 4개와 표별 권한 |
| 5 | 역할별 인증 수단 설정 | 비밀 저장소에서 설정하고 역할별 실제 접속 확인 |
| 6 | `db/preparation/90_inspect_installed.sql` | 표·제약·정책·소유자·역할 속성·소속·유효 권한 대조 |

아래는 **대상이 정해지고 사전 확인이 끝난 뒤** 소유자 연결로 실행하는 예제다. `backend` 폴더 기준이다. `gwonri_db_owner`는 이후 로컬 연결 서비스 파일에 마련할 예시 이름이며 아직 실제 접속 설정이 아니다. 서비스 파일에는 서버·DB·계정·TLS 설정을 두고 비밀번호는 별도의 접근 제한된 비밀 파일 또는 안전한 입력 경로로 제공한다. 비밀번호가 든 전체 접속 문자열을 명령행 인자에 넣지 않는다. 대상 DB 생성이나 비밀번호 설정 명령은 포함하지 않는다.

```bash
psql 'service=gwonri_db_owner' -X --set=ON_ERROR_STOP=1 \
  --file=db/preparation/00_preflight.sql

psql 'service=gwonri_db_owner' -X --set=ON_ERROR_STOP=1 --single-transaction \
  --command='SET LOCAL search_path = public' \
  --file=db/schema.sql \
  --file=db/rls_policies.sql \
  --file=db/knowledge_schema.sql \
  --file=db/roles.sql

psql 'service=gwonri_db_owner' -X --set=ON_ERROR_STOP=1 \
  --file=db/preparation/90_inspect_installed.sql
```

설치 1~4는 오류 시 중단하고 한 트랜잭션으로 취소되게 묶었다. 사전 조회는 자동 승인기가 아니므로 조회 출력을 검토한 뒤 설치 명령을 실행한다. `schema.sql`은 새 DB 전용이며 반복 적용 파일이 아니다. ORM 모델에서 생성되는 원본을 손으로 수정하지 않는다.

이미 v0.11.1 구조가 생성된 새 DB에 `v0.11_to_v0.11.1.sql`을 이어서 실행하지 않는다. 기존 DB의 이관은 현재 버전·위반 행·백업을 따로 확인한 뒤 필요한 이관 파일만 적용한다. `IF NOT EXISTS`는 기존 표의 구조가 같은지 검증해 주지 않는다. 기존 데이터를 자동 정리하거나 `DROP SCHEMA ... CASCADE`로 초기화하지 않는다.

## 3 계정 권한 계약

행별 접근 제한(RLS)은 사용자별로 조회·수정 가능한 행을 제한한다. 앱 연결에서는 트랜잭션 안에서 `set_config('app.user_id', 사용자UUID, true)`로 사용자 문맥을 설정하며 연결 풀에 다음 사용자 문맥이 남지 않게 한다.

| 역할 | 담당 용도 | 유지할 권한 |
| --- | --- | --- |
| `app_rw` | 시민 API | NOSUPERUSER·NOBYPASSRLS. 개인정보 표 SELECT·INSERT·UPDATE, 감사 로그 INSERT, 지식 표 SELECT. 업무 표 직접 DELETE 없음. |
| `app_auth` | 회원가입·로그인 | NOSUPERUSER·BYPASSRLS. users SELECT·INSERT·UPDATE와 감사 로그 INSERT. 사건·문서·파기 작업표 접근 없음. |
| `app_batch` | 보관기간 경과 자료 파기 | NOSUPERUSER·BYPASSRLS. 기존 파기 권한과 purge_tasks SELECT·INSERT·UPDATE·DELETE. 실제 그래프 저장 계정으로 확대하지 않음. |
| `app_knowledge_loader` | 공식 지식 자료 적재 | NOSUPERUSER·NOBYPASSRLS. knowledge 표 읽기·등록·수정·삭제. 시민 업무 표 접근 없음. |

설치 소유자는 위 네 앱 역할과 구분한다. 권한이 부족하면 담당자와 설치 경로를 조정하며 RLS 해제·앱 계정의 superuser 승격·관리자 접속값의 API 사용으로 해결하지 않는다. 비밀번호·관리자 접속값은 문서·채팅·Git·시험 로그에 기록하지 않는다.

## 4 체크포인트는 별도 단계로 준비

중간 실행 상태 저장소는 `langgraph_checkpoints` 스키마다. 이형민이 소유자 연결로 스키마를 만든 뒤 공식 `PostgresSaver.setup()`을 실행한다. 검증 버전은 `langgraph-checkpoint-postgres==3.2.0`, `langgraph-checkpoint==4.3.1`이다.

`setup()`은 동시 인덱스를 만들 수 있으므로 위 설치 트랜잭션에 넣지 않는다. 공식 autocommit 연결 및 전용 search_path를 사용하고, 준비된 표 네 개를 확인한 다음 `checkpoint_roles.sql`을 별도로 실행한다. 이 파일 자체에 BEGIN·COMMIT이 있다.

- `checkpoint_migrations`: app_batch의 SELECT만 허용.
- `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`: app_batch의 SELECT·DELETE만 허용.
- app_rw·app_auth의 스키마·표 접근은 차단.

체크포인트 접속이 없으면 미실행/대기 상태로 기록한다. 실제 Graph 쓰기 계정 설계·모델 실행·API 연동 완료를 이 DB 준비 결과로 표시하지 않는다.

## 5 실제 대상에서 다시 확인할 완료 기준

- [ ] 선택한 DB·PostgreSQL·pgvector 버전·원본 SQL 해시를 기록했다.
- [ ] 업무 표 11개와 지식 표 2개, 개인정보 표 9개의 RLS·FORCE RLS, 역할 4개의 속성·소속·소유권을 확인했다.
- [ ] 역할별 실제 접속으로 본인·타인·문맥 없음과 잘못된 소유자 등록을 시험했다. 조회용 점검 SQL만으로 동작 PASS를 쓰지 않았다.
- [ ] UNKNOWN의 가짜 값·출처를 직접 INSERT·UPDATE하여 DB가 거부함을 확인했다.
- [ ] app_rw·app_auth의 purge_tasks 접근 거부와 app_batch 처리를 확인했다.
- [ ] 합성 벡터 저장·거리 검색을 확인했다. 실제 모델 품질 검증과 구분했다.
- [ ] 백업을 별도 새 DB에 복원하고 표·제약·권한·행 격리를 다시 확인했다.
- [ ] 체크포인트를 쓰는 환경이면 공식 setup·권한·파기 재시도를 확인했다.
- [ ] 로그에는 PASS/FAIL/NOT RUN/BLOCKED, 실행 명령·대상·버전·제한을 함께 기록했다.

`pg_dump`는 한 DB를 백업하며 클러스터 공통 역할·비밀번호를 포함하지 않는다. 다른 서버 복구에는 검토된 역할 정의·권한·별도 비밀 전달·확장 설치가 필요하다. `template0` 기반 새 시험 DB에 `pg_restore --exit-on-error --dbname=새DB ...`로 복원하며, 별도 이름의 DB에 복원할 때 `--create`를 섞지 않는다. `--no-owner`·`--no-acl`로 생략한 항목은 복원 완료로 표시하지 않는다.

현재 남은 `unknown_status` 상태 제한 차이는 김교민의 기존 후속 검토로 유지한다. `unknown_has_no_source_detail` 직접 방어 검증이 이 상태 제한 차이까지 해결했다는 뜻은 아니다. 제약을 추가해야 하면 기존 행 영향과 최소 이관·되돌리기안을 별도로 검토한다.

## 6 이번 새 로컬 검증 근거

2026-11-12 생성한 별도 임시 PostgreSQL **16.2**와 pgvector **0.6.2**에서 원본 SQL을 그대로 설치했다. 이 버전은 시험 환경 기록이며 새 공용 DB의 배포 버전을 고정하지 않는다.

| 검증 그룹 | 결과 | 범위 |
| --- | --- | --- |
| 설치·역할·RLS·UNKNOWN·파기 권한·벡터·백업 복원 | 6 PASS | RLS 실제 행 시험은 대표 3개 표. 합성 384차원 벡터 2개. 같은 새 임시 클러스터의 별도 새 DB에 복원. |
| 기존 체크포인트 검증 새 실행 | 9 PASS | 실제 DB 시험 5개와 잘못된 대상·버전 차단 검사 4개. |

두 그룹에서 실패·제외는 0개다. 15는 pytest 사례 수이며 UNKNOWN의 잘못된 SQL 14회와 파기 접근 거부 8회는 사례 수에 더하지 않는다. 원시 출력·JUnit·백업 비교·원본 해시는 DB 준비 묶음의 `verification/`에 있다.

조회 파일 두 개를 미설치·설치 후에 각각 읽기 전용 트랜잭션으로 실행한 4회와 실제 psql 설치 1회도 별도 절차 점검으로 통과했다. 13개 표·9개 RLS 표·4개 역할, 역할×표 52행의 권한 값 260개를 대조했다. 이 5회는 위 pytest 사례 수에 더하지 않는다. 설치 시험은 비밀번호 없는 새 loopback 임시 연결을 사용했으며 README의 연결 서비스 파일 자체는 아직 구성·검증하지 않았다. SQL 순서·단일 트랜잭션·search_path는 동일하게 실행했다. 정상 설치 경로 시험이며 설치 실패 시의 롤백 시험까지 포함하지 않는다. 증거는 `verification/preparation_checks/`에 있다.

공용 DB 생성·원격 접속·다른 서버로의 전체 재해 복구·실제 모델·Graph·API·화면·전체 회귀 시험은 이번 실행에 포함되지 않는다. 새 조회 SQL의 실행 점검은 별도 기록하고 위 15개에 합산하지 않는다.

## 공식 참고

아래 PostgreSQL 17 문서는 기능 설명 근거이며 배포 버전 확정이 아니다. 선택 환경의 실제 버전 문서도 다시 확인한다.

- 역할 생성과 BYPASSRLS: https://www.postgresql.org/docs/17/sql-createrole.html
- 제공 확장과 설치 상태: https://www.postgresql.org/docs/17/view-pg-available-extensions.html
- 설치된 확장 위치: https://www.postgresql.org/docs/17/catalog-pg-extension.html
- 백업 범위·버전 호환성: https://www.postgresql.org/docs/17/app-pgdump.html
- 새 DB 복원 옵션: https://www.postgresql.org/docs/17/app-pgrestore.html
- 비밀값 없는 연결 서비스명: https://www.postgresql.org/docs/17/libpq-pgservice.html
- 비밀번호 파일과 접근 권한: https://www.postgresql.org/docs/17/libpq-pgpass.html
