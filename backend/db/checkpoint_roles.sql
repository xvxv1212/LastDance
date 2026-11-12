-- 체크포인트 파기 최소 권한 — langgraph-checkpoint-postgres 3.2.0 / checkpoint 4.3.1
-- 실행 전: 소유자가 langgraph_checkpoints 스키마 생성 후 PostgresSaver.setup() 실행.
-- db/roles.sql의 app_batch/app_rw/app_auth가 이미 있어야 한다.
-- 이 파일은 저장소 표를 직접 만들거나 migration 표를 수정하지 않는다.
-- HJ_CHECKPOINT_DATABASE_URL은 app_batch 및 options=-csearch_path=langgraph_checkpoints를 사용한다.
-- 2026-11-12 사용자 purge_tasks 채택 승인 후속. 운영 적용은 환경·백업·권한 확인 후 별도 실행 기록.
BEGIN;
REVOKE ALL ON SCHEMA langgraph_checkpoints FROM PUBLIC, app_rw, app_auth;
REVOKE ALL ON ALL TABLES IN SCHEMA langgraph_checkpoints FROM PUBLIC, app_rw, app_auth;
GRANT USAGE ON SCHEMA langgraph_checkpoints TO app_batch;
GRANT SELECT ON langgraph_checkpoints.checkpoint_migrations TO app_batch;
GRANT SELECT, DELETE ON
    langgraph_checkpoints.checkpoints,
    langgraph_checkpoints.checkpoint_blobs,
    langgraph_checkpoints.checkpoint_writes
TO app_batch;
COMMIT;
