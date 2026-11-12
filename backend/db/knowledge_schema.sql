-- =====================================================================
-- 권리채비 AI : 벡터 지식 DB (v0.9 팀 결정 6 — A안: 같은 PostgreSQL 안 pgvector, 별도 스키마)
--
-- - 업무 DB(public 스키마, 시민 개인 기록)와 '스키마'로 분리한다. 업무 테이블과 FK 를 두지 않는다.
--   보고서는 청크 ID 문자열 + 인용 당시 출처 사본만 저장한다(v0.6 결정 유지).
-- - 공개 공식 자료(법령·고시·분쟁해결기준)만 담는다 → RLS 대상 아님. 시민 개인정보 금지.
-- - 임베딩 차원은 임베딩 모델이 정해진 뒤 고정한다. 그 전까지는 차원 없는 vector 로 두고,
--   차원을 정하면 아래 '차원 고정·색인' 블록을 실행한다(HNSW 색인은 고정 차원이 필요).
-- - 실행 순서: schema.sql → rls_policies.sql → knowledge_schema.sql → roles.sql
-- 되돌리기: DROP SCHEMA knowledge CASCADE;  (업무 DB 에 영향 없음. vector 확장은 남겨도 무방)
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS knowledge;

CREATE TABLE IF NOT EXISTS knowledge.sources (
    id             VARCHAR(100) PRIMARY KEY,               -- 출처 ID(예: fitness-standard)
    source_type    VARCHAR(30)  NOT NULL,                   -- LAW/ENFORCEMENT_DECREE/NOTICE/DISPUTE_STANDARD …
    title          VARCHAR(300) NOT NULL,
    issuer         VARCHAR(100) NOT NULL,                   -- 발행 기관
    url            VARCHAR(500) NOT NULL,
    version_label  VARCHAR(50)  NOT NULL,
    is_active      BOOLEAN      NOT NULL DEFAULT true,      -- 폐지·개정 시 false → 인용 보류
    effective_date DATE,                                    -- 시행일
    checked_on     DATE,                                    -- 마지막 확인일
    local_file     VARCHAR(300),                            -- 원문 사본 경로
    loaded_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE knowledge.sources IS '공식 출처(공개 자료). 업무 DB 와 FK 없음';

CREATE TABLE IF NOT EXISTS knowledge.chunks (
    id          VARCHAR(150) PRIMARY KEY,                   -- 청크 ID(보고서 인용이 이 문자열을 저장)
    source_id   VARCHAR(100) NOT NULL REFERENCES knowledge.sources(id) ON DELETE CASCADE,
    chunk_index INTEGER      NOT NULL CHECK (chunk_index >= 0),
    article_ref VARCHAR(100),                               -- 조항(예: 별표 2)
    content     TEXT         NOT NULL,
    embedding   vector,                                     -- 차원 미정(모델 결정 후 고정)
    embedding_model VARCHAR(100),                           -- 어떤 모델로 만든 벡터인지(v0.10: '모델@리비전')
    UNIQUE (source_id, chunk_index)
);
COMMENT ON TABLE knowledge.chunks IS '검색 단위 청크와 임베딩. 같은 DB, 다른 스키마(pgvector)';

-- ---------- 차원 고정·색인(임베딩 모델 검증 후. 초기 검증 후보: multilingual-e5-small 384차원) ----------
-- 작은 검색 시험에는 색인이 필요 없다. 모델을 확정한 뒤 아래를 실행한다.
-- ALTER TABLE knowledge.chunks ALTER COLUMN embedding TYPE vector(384);
-- CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON knowledge.chunks USING hnsw (embedding vector_cosine_ops);
