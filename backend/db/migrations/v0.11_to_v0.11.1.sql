-- =====================================================================
-- 변환 v0.11 → v0.11.1 (한 트랜잭션, 기존 데이터 변경 없음) — DB 담당 김교민, 2026-11-12
-- 미확인(UNKNOWN) 사실에 정규화 값·AI 최초값·문서·페이지·원문 위치·구절이 남지 않도록 CHECK 추가.
-- 이유: value_text 만 막혀 있어 value_normalized 에 {"amount": 0} 같은 가짜 값이 저장될 수 있었다(재현됨).
-- 승인된 UNKNOWN 계약(UNKNOWN/null 유지)을 DB 에서도 보장한다.
-- 위반 행이 1건이라도 있으면 '중단'한다. 값을 지우거나 근거 유형을 바꾸는 자동 정리는 하지 않는다
-- (어느 쪽이 맞는지는 사용자·서비스 담당이 판단할 문제).
-- =====================================================================
BEGIN;

DO $$
DECLARE n bigint;
BEGIN
    SELECT count(*) INTO n FROM extracted_facts
     WHERE source_kind = 'UNKNOWN'
       AND (value_normalized IS NOT NULL OR ai_value_text IS NOT NULL OR document_id IS NOT NULL
            OR page_no IS NOT NULL OR offset_start IS NOT NULL OR offset_end IS NOT NULL
            OR source_span IS NOT NULL);
    IF n > 0 THEN
        RAISE EXCEPTION '변환 중단: 미확인(UNKNOWN) 사실 중 값·출처 세부가 남은 행 %건. 자동 정리하지 않습니다(조회: 이 파일 하단 주석).', n;
    END IF;
END $$;

ALTER TABLE extracted_facts ADD CONSTRAINT ck_extracted_facts_unknown_has_no_source_detail
    CHECK (source_kind <> 'UNKNOWN' OR (value_normalized IS NULL AND ai_value_text IS NULL
           AND document_id IS NULL AND page_no IS NULL AND offset_start IS NULL
           AND offset_end IS NULL AND source_span IS NULL));

COMMIT;

-- 위반 행 확인(읽기 전용, 값 원문은 보지 않고 어떤 칸이 차 있는지만):
-- SELECT id, check_session_id, fact_key,
--        value_normalized IS NOT NULL AS has_normalized, ai_value_text IS NOT NULL AS has_ai_value,
--        document_id IS NOT NULL AS has_doc, page_no IS NOT NULL AS has_page,
--        offset_start IS NOT NULL AS has_offset, source_span IS NOT NULL AS has_span
--   FROM extracted_facts WHERE source_kind = 'UNKNOWN' AND (value_normalized IS NOT NULL OR ai_value_text IS NOT NULL
--        OR document_id IS NOT NULL OR page_no IS NOT NULL OR offset_start IS NOT NULL OR offset_end IS NOT NULL
--        OR source_span IS NOT NULL);
