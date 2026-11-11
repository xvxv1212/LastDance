-- 권리채비 AI 업무 DB PostgreSQL 스키마 (models.py 에서 자동 생성, 직접 수정 금지)
-- v0.6: 공식 지식(벡터)은 별도 벡터 지식 DB → pgvector 확장 불필요

CREATE TABLE users (
	id UUID NOT NULL, 
	email VARCHAR(254), 
	password_hash VARCHAR(255), 
	display_name VARCHAR(50), 
	role VARCHAR(30) DEFAULT 'CITIZEN' NOT NULL, 
	status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL, 
	failed_login_count INTEGER DEFAULT 0 NOT NULL, 
	locked_until TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT ck_users_login_required CHECK (status = 'WITHDRAWN' OR (email IS NOT NULL AND password_hash IS NOT NULL)), 
	CONSTRAINT ck_users_withdrawn_has_no_identity CHECK (status <> 'WITHDRAWN' OR (email IS NULL AND password_hash IS NULL AND display_name IS NULL AND deleted_at IS NOT NULL)), 
	CONSTRAINT ck_users_failed_login_count_non_negative CHECK (failed_login_count >= 0), 
	CONSTRAINT uq_users_email UNIQUE (email), 
	CONSTRAINT ck_users_role CHECK (role IN ('CITIZEN', 'COUNSELOR', 'ADMIN')), 
	CONSTRAINT ck_users_status CHECK (status IN ('ACTIVE', 'DORMANT', 'WITHDRAWN'))
);
COMMENT ON TABLE users IS '사용자: 서비스 회원 계정(탈퇴 시 식별정보를 지운 빈 행으로 남김)';
COMMENT ON COLUMN users.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN users.email IS '이메일: 로그인 ID. 소문자로 정규화하여 저장. v0.9: 탈퇴 시 NULL(같은 이메일 재가입 허용)';
COMMENT ON COLUMN users.password_hash IS '비밀번호 해시: Argon2id 해시(평문 저장 금지). v0.9: 탈퇴 시 NULL';
COMMENT ON COLUMN users.display_name IS '표시 이름: 실명 아님. 선택 입력. 탈퇴 시 NULL';
COMMENT ON COLUMN users.role IS '역할: CITIZEN/COUNSELOR/ADMIN. v0.9 MVP 는 CITIZEN 만 사용(상담사·관리자 원문 열람 없음)';
COMMENT ON COLUMN users.status IS '계정 상태: ACTIVE/DORMANT/WITHDRAWN. ACTIVE 가 아니면 모든 API 거부';
COMMENT ON COLUMN users.failed_login_count IS '로그인 실패 횟수: 연속 실패 횟수(성공 시 0). 기준 횟수에 이르면 잠금';
COMMENT ON COLUMN users.locked_until IS '잠금 해제 시각: 연속 실패로 잠긴 경우 이 시각까지 로그인 거부';
COMMENT ON COLUMN users.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';
COMMENT ON COLUMN users.updated_at IS '수정일시: 마지막 변경 시각';
COMMENT ON COLUMN users.deleted_at IS '탈퇴일시: 탈퇴 시각. 감사 로그 보관기간이 지나면 이 빈 행도 파기';

CREATE TABLE audit_logs (
	id UUID NOT NULL, 
	actor_id UUID, 
	action VARCHAR(30) NOT NULL, 
	target_type VARCHAR(50) NOT NULL, 
	target_id UUID, 
	ip_hash VARCHAR(64), 
	detail JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_audit_logs PRIMARY KEY (id), 
	CONSTRAINT fk_audit_logs_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id), 
	CONSTRAINT ck_audit_logs_action CHECK (action IN ('CREATE', 'READ_SENSITIVE', 'UPDATE', 'DELETE', 'EXPORT', 'CONSENT', 'PURGE', 'AI_CALL'))
);
CREATE INDEX ix_audit_logs_actor_id_created_at ON audit_logs (actor_id, created_at);
CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);
CREATE INDEX ix_audit_logs_target_type_target_id ON audit_logs (target_type, target_id);
COMMENT ON TABLE audit_logs IS '감사 로그: 민감 자원 생성·열람·수정·삭제·동의·파기·외부 AI 호출 감사 로그(수정·삭제 불가, 원문 미기록)';
COMMENT ON COLUMN audit_logs.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN audit_logs.actor_id IS '행위자 ID: 시스템 작업은 NULL';
COMMENT ON COLUMN audit_logs.action IS '행위: CREATE/READ_SENSITIVE/UPDATE/DELETE/EXPORT/CONSENT/PURGE/AI_CALL';
COMMENT ON COLUMN audit_logs.target_type IS '대상 테이블: 대상 테이블';
COMMENT ON COLUMN audit_logs.target_id IS '대상 ID: 파기 후에도 남는 식별자(개인정보 아님)';
COMMENT ON COLUMN audit_logs.ip_hash IS 'IP 해시: 원본 IP 대신 솔트 해시';
COMMENT ON COLUMN audit_logs.detail IS '상세: 건수·코드만(AI_CALL: 노드·상태·외부 전송 여부·처리 근거·차단 코드). 원문 기록 금지';
COMMENT ON COLUMN audit_logs.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';

CREATE TABLE check_sessions (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	entry_type VARCHAR(30) NOT NULL, 
	situation_text TEXT, 
	status VARCHAR(30) DEFAULT 'DRAFT' NOT NULL, 
	facts_confirmed_at TIMESTAMP WITH TIME ZONE, 
	thread_id VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_check_sessions PRIMARY KEY (id), 
	CONSTRAINT uq_check_sessions_id_user_id UNIQUE (id, user_id), 
	CONSTRAINT fk_check_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id), 
	CONSTRAINT ck_check_sessions_entry_type CHECK (entry_type IN ('REFUND_CONCERN', 'DISPUTE_OCCURRED', 'PRE_CONTRACT')), 
	CONSTRAINT ck_check_sessions_status CHECK (status IN ('DRAFT', 'FACTS_EXTRACTED', 'FACTS_CONFIRMED', 'REPORTED', 'CONVERTED', 'ABANDONED')), 
	CONSTRAINT uq_check_sessions_thread_id UNIQUE (thread_id)
);
CREATE INDEX ix_check_sessions_updated_at ON check_sessions (updated_at);
CREATE INDEX ix_check_sessions_user_id_created_at ON check_sessions (user_id, created_at);
COMMENT ON TABLE check_sessions IS 'AI 점검 세션: AI 통합 점검 1회분(상황 입력~보고서). 사건 연결은 cases.origin_session_id 로만 관리';
COMMENT ON COLUMN check_sessions.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN check_sessions.user_id IS '사용자 ID: 점검 소유자, users.id';
COMMENT ON COLUMN check_sessions.entry_type IS '진입 경로: REFUND_CONCERN/DISPUTE_OCCURRED/PRE_CONTRACT';
COMMENT ON COLUMN check_sessions.situation_text IS '상황 설명: 생활언어 입력 원문(고유식별정보 마스킹 후 저장). v0.8: PDF 만으로 시작하면 NULL(이때는 계약서 PDF 가 있어야 사실 추출 가능)';
COMMENT ON COLUMN check_sessions.status IS '진행 상태: DRAFT→FACTS_EXTRACTED→FACTS_CONFIRMED→REPORTED→CONVERTED / ABANDONED';
COMMENT ON COLUMN check_sessions.facts_confirmed_at IS '사실 승인일시: 사용자가 추출 사실 전체를 승인한 시각(DEC-01: 사건 전 승인 기록)';
COMMENT ON COLUMN check_sessions.thread_id IS 'AI 스레드 ID: LangGraph thread_id(추측 불가 랜덤). 구 agent_threads 대체, 체크포인트 파기 기준';
COMMENT ON COLUMN check_sessions.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';
COMMENT ON COLUMN check_sessions.updated_at IS '수정일시: 마지막 변경 시각';
COMMENT ON COLUMN check_sessions.deleted_at IS '삭제일시: 논리 삭제 시각. 값이 있으면 조회에서 제외, 파기 배치 대상';

CREATE TABLE user_consents (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	consent_type VARCHAR(30) NOT NULL, 
	policy_version VARCHAR(20) NOT NULL, 
	agreed BOOLEAN NOT NULL, 
	agreed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	withdrawn_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_user_consents PRIMARY KEY (id), 
	CONSTRAINT fk_user_consents_user_id_users FOREIGN KEY(user_id) REFERENCES users (id), 
	CONSTRAINT ck_user_consents_consent_type CHECK (consent_type IN ('PRIVACY_REQUIRED', 'SENSITIVE_DOCUMENT', 'OVERSEAS_TRANSFER'))
);
CREATE INDEX ix_user_consents_user_id_consent_type_agreed_at ON user_consents (user_id, consent_type, agreed_at);
COMMENT ON TABLE user_consents IS '사용자 동의 이력: 개인정보·문서·국외이전 동의 및 철회 이력(append-only)';
COMMENT ON COLUMN user_consents.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN user_consents.user_id IS '사용자 ID: users.id';
COMMENT ON COLUMN user_consents.consent_type IS '동의 종류: PRIVACY_REQUIRED/SENSITIVE_DOCUMENT/OVERSEAS_TRANSFER';
COMMENT ON COLUMN user_consents.policy_version IS '약관 버전: 동의 당시 고지문 버전(예: 2026-11-v1)';
COMMENT ON COLUMN user_consents.agreed IS '동의 여부: true=동의, false=철회';
COMMENT ON COLUMN user_consents.agreed_at IS '처리일시: 동의 또는 철회 처리 시각';
COMMENT ON COLUMN user_consents.withdrawn_at IS '철회일시: 철회 시에만 기록';

CREATE TABLE inspection_reports (
	id UUID NOT NULL, 
	check_session_id UUID NOT NULL, 
	version INTEGER NOT NULL, 
	status VARCHAR(30) DEFAULT 'DRAFT' NOT NULL, 
	summary_text TEXT NOT NULL, 
	items JSONB NOT NULL, 
	approved_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_inspection_reports PRIMARY KEY (id), 
	CONSTRAINT uq_inspection_reports_check_session_id_version UNIQUE (check_session_id, version), 
	CONSTRAINT uq_inspection_reports_id_check_session_id UNIQUE (id, check_session_id), 
	CONSTRAINT ck_inspection_reports_version_positive CHECK (version >= 1), 
	CONSTRAINT fk_inspection_reports_check_session_id_check_sessions FOREIGN KEY(check_session_id) REFERENCES check_sessions (id) ON DELETE CASCADE, 
	CONSTRAINT ck_inspection_reports_status CHECK (status IN ('DRAFT', 'APPROVED', 'SUPERSEDED'))
);
COMMENT ON TABLE inspection_reports IS '점검 보고서: 3분류 점검 보고서(버전 관리, 승인 후 본문 불변). 항목·인용·검증 결과는 items JSON';
COMMENT ON COLUMN inspection_reports.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN inspection_reports.check_session_id IS '점검 세션 ID: check_sessions.id';
COMMENT ON COLUMN inspection_reports.version IS '버전: 세션별 1부터 증가';
COMMENT ON COLUMN inspection_reports.status IS '상태: DRAFT/APPROVED/SUPERSEDED';
COMMENT ON COLUMN inspection_reports.summary_text IS '요약: 보고서 머리 요약';
COMMENT ON COLUMN inspection_reports.items IS '보고서 항목: JSON 배열(구 report_items·report_citations): 분류·내용·연결 사실·보류 사유·인용(청크 ID·인용문·검증 결과·인용 당시 출처 제목/URL/시행일)';
COMMENT ON COLUMN inspection_reports.approved_at IS '승인일시: 사용자 최종 확인 시각';
COMMENT ON COLUMN inspection_reports.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';

CREATE TABLE cases (
	id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	title VARCHAR(100) NOT NULL, 
	case_type VARCHAR(30) NOT NULL, 
	status VARCHAR(30) DEFAULT 'OPEN' NOT NULL, 
	origin_session_id UUID, 
	business_name VARCHAR(100), 
	thread_id VARCHAR(64), 
	verified_facts JSONB, 
	snapshot_version INTEGER, 
	snapshot_report_id UUID, 
	snapshot_approved_at TIMESTAMP WITH TIME ZONE, 
	unverified_items JSONB DEFAULT '[]' NOT NULL, 
	consultation_prep JSONB, 
	closed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_cases PRIMARY KEY (id), 
	CONSTRAINT uq_cases_id_owner_id UNIQUE (id, owner_id), 
	CONSTRAINT fk_cases_origin_session_owner FOREIGN KEY(origin_session_id, owner_id) REFERENCES check_sessions (id, user_id), 
	CONSTRAINT ck_cases_snapshot_complete CHECK ((verified_facts IS NULL) = (snapshot_version IS NULL) AND (verified_facts IS NULL) = (snapshot_approved_at IS NULL)), 
	CONSTRAINT ck_cases_snapshot_version_positive CHECK (snapshot_version IS NULL OR snapshot_version >= 1), 
	CONSTRAINT fk_cases_snapshot_report_same_session FOREIGN KEY(snapshot_report_id, origin_session_id) REFERENCES inspection_reports (id, check_session_id), 
	CONSTRAINT ck_cases_snapshot_report_needs_origin CHECK (snapshot_report_id IS NULL OR origin_session_id IS NOT NULL), 
	CONSTRAINT fk_cases_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id), 
	CONSTRAINT ck_cases_case_type CHECK (case_type IN ('FITNESS_REFUND', 'OTHER_CONSUMER')), 
	CONSTRAINT ck_cases_status CHECK (status IN ('OPEN', 'PREPARING', 'CONSULTING', 'CLOSED')), 
	CONSTRAINT uq_cases_origin_session_id UNIQUE (origin_session_id), 
	CONSTRAINT uq_cases_thread_id UNIQUE (thread_id)
);
CREATE INDEX ix_cases_owner_id_status ON cases (owner_id, status);
COMMENT ON TABLE cases IS '사건: 시민의 분쟁 사건. 점검 전환이면 origin_session_id(UNIQUE), 신규 직행이면 NULL. 확인 사실 스냅샷·미확인 정보·상담 준비자료는 컬럼(JSON)으로 보관';
COMMENT ON COLUMN cases.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN cases.owner_id IS '소유자 ID: users.id';
COMMENT ON COLUMN cases.title IS '사건 제목: 2~100자';
COMMENT ON COLUMN cases.case_type IS '사건 유형: FITNESS_REFUND/OTHER_CONSUMER';
COMMENT ON COLUMN cases.status IS '상태: OPEN/PREPARING/CONSULTING/CLOSED';
COMMENT ON COLUMN cases.origin_session_id IS '원본 점검 ID: 전환 출처(UNIQUE: 점검 1건 → 사건 최대 1건), 신규 사건은 NULL';
COMMENT ON COLUMN cases.business_name IS '상대 사업자명: 상대 사업자명';
COMMENT ON COLUMN cases.thread_id IS 'AI 스레드 ID: 사건에서 시작한 LangGraph thread_id(신규 직행 포함). 구 agent_threads 대체';
COMMENT ON COLUMN cases.verified_facts IS '확인 사실: 사건의 정본 사실 묶음 JSON(키·값·근거 유형·fact_id). 구 verified_snapshots.payload';
COMMENT ON COLUMN cases.snapshot_version IS '스냅샷 버전: 확인 사실 버전(전환 시 1). 버전 변경 이력은 audit_logs';
COMMENT ON COLUMN cases.snapshot_report_id IS '근거 보고서 ID: 스냅샷을 만든 승인 보고서(원본 점검의 보고서만, v0.8 복합 FK). 신규 사건 직행이면 NULL';
COMMENT ON COLUMN cases.snapshot_approved_at IS '스냅샷 승인일시: 사용자가 사실을 승인한 시각';
COMMENT ON COLUMN cases.unverified_items IS '미확인 정보: JSON 배열[{id, content, status, resolved_at}]. 구 unverified_items 테이블';
COMMENT ON COLUMN cases.consultation_prep IS '상담 준비자료: 최신본 JSON{version, status, agency_code, summary_text, checklist, questions, created_at}. 구 consultation_preps';
COMMENT ON COLUMN cases.closed_at IS '종결일시: 종결일시';
COMMENT ON COLUMN cases.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';
COMMENT ON COLUMN cases.updated_at IS '수정일시: 마지막 변경 시각';
COMMENT ON COLUMN cases.deleted_at IS '삭제일시: 논리 삭제 시각. 값이 있으면 조회에서 제외, 파기 배치 대상';

CREATE TABLE case_events (
	id UUID NOT NULL, 
	case_id UUID NOT NULL, 
	occurred_on DATE NOT NULL, 
	actor VARCHAR(30) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	description TEXT, 
	is_verified BOOLEAN DEFAULT false NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_case_events PRIMARY KEY (id), 
	CONSTRAINT uq_case_events_id_case_id UNIQUE (id, case_id), 
	CONSTRAINT fk_case_events_case_id_cases FOREIGN KEY(case_id) REFERENCES cases (id) ON DELETE CASCADE, 
	CONSTRAINT ck_case_events_actor CHECK (actor IN ('USER', 'BUSINESS', 'AGENCY', 'OTHER'))
);
CREATE INDEX ix_case_events_case_id_occurred_on ON case_events (case_id, occurred_on);
COMMENT ON TABLE case_events IS '사건 타임라인: 사건 타임라인(언제·누가·무엇을)';
COMMENT ON COLUMN case_events.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN case_events.case_id IS '사건 ID: cases.id';
COMMENT ON COLUMN case_events.occurred_on IS '발생일: 미래 날짜 불가';
COMMENT ON COLUMN case_events.actor IS '행위 주체: USER/BUSINESS/AGENCY/OTHER';
COMMENT ON COLUMN case_events.title IS '내용 제목: 예: 해지·환불 요청';
COMMENT ON COLUMN case_events.description IS '상세 내용: 상세 내용';
COMMENT ON COLUMN case_events.is_verified IS '사용자 확인 여부: 사용자 확인 여부';
COMMENT ON COLUMN case_events.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';

CREATE TABLE documents (
	id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	check_session_id UUID, 
	case_id UUID, 
	original_name VARCHAR(200) NOT NULL, 
	mime_type VARCHAR(100) NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	storage_key VARCHAR(100) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	page_count INTEGER, 
	retention_until DATE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_documents PRIMARY KEY (id), 
	CONSTRAINT ck_documents_size_range CHECK (size_bytes > 0 AND size_bytes <= 10485760), 
	CONSTRAINT ck_documents_page_count_positive CHECK (page_count IS NULL OR page_count >= 1), 
	CONSTRAINT ck_documents_parent_required CHECK (check_session_id IS NOT NULL OR case_id IS NOT NULL), 
	CONSTRAINT fk_documents_session_owner FOREIGN KEY(check_session_id, owner_id) REFERENCES check_sessions (id, user_id) ON DELETE CASCADE, 
	CONSTRAINT fk_documents_case_owner FOREIGN KEY(case_id, owner_id) REFERENCES cases (id, owner_id) ON DELETE CASCADE, 
	CONSTRAINT uq_documents_id_check_session_id UNIQUE (id, check_session_id), 
	CONSTRAINT uq_documents_id_case_id UNIQUE (id, case_id), 
	CONSTRAINT fk_documents_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id), 
	CONSTRAINT uq_documents_storage_key UNIQUE (storage_key)
);
CREATE INDEX ix_documents_case_id ON documents (case_id);
CREATE INDEX ix_documents_check_session_id ON documents (check_session_id);
CREATE INDEX ix_documents_retention_until ON documents (retention_until);
COMMENT ON TABLE documents IS '업로드 문서: 업로드 파일 메타데이터(본문은 암호화 객체 저장소). 점검·사건 중 최소 하나에 소속';
COMMENT ON COLUMN documents.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN documents.owner_id IS '소유자 ID: users.id, 부모 점검·사건 소유자와 같아야 함';
COMMENT ON COLUMN documents.check_session_id IS '점검 세션 ID: 업로드된 점검';
COMMENT ON COLUMN documents.case_id IS '사건 ID: 사건 전환·증거 첨부 시 연결';
COMMENT ON COLUMN documents.original_name IS '원본 파일명: 표시용(경로 문자 제거). 저장 경로로 사용 금지';
COMMENT ON COLUMN documents.mime_type IS 'MIME 유형: MVP 는 application/pdf 만 허용';
COMMENT ON COLUMN documents.size_bytes IS '파일 크기: 바이트, 최대 10MB';
COMMENT ON COLUMN documents.storage_key IS '저장소 키: 추측 불가 랜덤 키(객체 저장소 경로)';
COMMENT ON COLUMN documents.sha256 IS 'SHA-256: 파일 무결성 해시';
COMMENT ON COLUMN documents.page_count IS '페이지 수: PDF 파싱 후 기록';
COMMENT ON COLUMN documents.retention_until IS '보관 기한: 정보용. v0.8: 문서 단독 파기 안 함, 사건·점검과 함께 파기';
COMMENT ON COLUMN documents.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';
COMMENT ON COLUMN documents.deleted_at IS '삭제일시: 논리 삭제 시각. 값이 있으면 조회에서 제외, 파기 배치 대상';

CREATE TABLE evidences (
	id UUID NOT NULL, 
	case_id UUID NOT NULL, 
	event_id UUID, 
	document_id UUID, 
	evidence_type VARCHAR(30) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	description TEXT, 
	obtained_on DATE, 
	status VARCHAR(30) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_evidences PRIMARY KEY (id), 
	CONSTRAINT fk_evidences_event_same_case FOREIGN KEY(event_id, case_id) REFERENCES case_events (id, case_id), 
	CONSTRAINT fk_evidences_document_same_case FOREIGN KEY(document_id, case_id) REFERENCES documents (id, case_id), 
	CONSTRAINT fk_evidences_case_id_cases FOREIGN KEY(case_id) REFERENCES cases (id) ON DELETE CASCADE, 
	CONSTRAINT ck_evidences_evidence_type CHECK (evidence_type IN ('CONTRACT', 'PAYMENT_RECEIPT', 'MESSAGE', 'CALL_RECORD', 'PHOTO', 'NOTICE', 'OTHER')), 
	CONSTRAINT ck_evidences_status CHECK (status IN ('HELD', 'MISSING', 'REQUESTED'))
);
CREATE INDEX ix_evidences_case_id_status ON evidences (case_id, status);
COMMENT ON TABLE evidences IS '증거: 사건 증거 목록(보유·미보유·요청). 타임라인·문서는 같은 사건 것만 연결';
COMMENT ON COLUMN evidences.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN evidences.case_id IS '사건 ID: cases.id';
COMMENT ON COLUMN evidences.event_id IS '관련 타임라인 ID: 같은 사건의 case_events.id';
COMMENT ON COLUMN evidences.document_id IS '첨부 문서 ID: 같은 사건의 documents.id';
COMMENT ON COLUMN evidences.evidence_type IS '증거 유형: CONTRACT/PAYMENT_RECEIPT/MESSAGE/CALL_RECORD/PHOTO/NOTICE/OTHER';
COMMENT ON COLUMN evidences.title IS '증거명: 증거명';
COMMENT ON COLUMN evidences.description IS '설명: 설명';
COMMENT ON COLUMN evidences.obtained_on IS '확보일: 확보일';
COMMENT ON COLUMN evidences.status IS '보유 상태: HELD/MISSING/REQUESTED';
COMMENT ON COLUMN evidences.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';
COMMENT ON COLUMN evidences.updated_at IS '수정일시: 마지막 변경 시각';

CREATE TABLE extracted_facts (
	id UUID NOT NULL, 
	check_session_id UUID NOT NULL, 
	fact_key VARCHAR(30) NOT NULL, 
	value_text TEXT, 
	value_normalized JSONB, 
	source_kind VARCHAR(30) NOT NULL, 
	document_id UUID, 
	page_no INTEGER, 
	offset_start INTEGER, 
	offset_end INTEGER, 
	source_span TEXT, 
	status VARCHAR(30) DEFAULT 'EXTRACTED' NOT NULL, 
	has_conflict BOOLEAN DEFAULT false NOT NULL, 
	ai_value_text TEXT, 
	confirmed_by UUID, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_extracted_facts PRIMARY KEY (id), 
	CONSTRAINT ck_extracted_facts_document_source_required CHECK (source_kind <> 'DOCUMENT' OR document_id IS NOT NULL), 
	CONSTRAINT ck_extracted_facts_unknown_has_no_value CHECK ((source_kind = 'UNKNOWN') = (value_text IS NULL)), 
	CONSTRAINT ck_extracted_facts_page_no_positive CHECK (page_no IS NULL OR page_no >= 1), 
	CONSTRAINT ck_extracted_facts_offset_pair CHECK ((offset_start IS NULL) = (offset_end IS NULL)), 
	CONSTRAINT ck_extracted_facts_offset_range CHECK (offset_start IS NULL OR (offset_start >= 0 AND offset_end >= offset_start)), 
	CONSTRAINT ck_extracted_facts_confirmation_complete CHECK ((status = 'EXTRACTED') = (confirmed_at IS NULL) AND (confirmed_at IS NULL) = (confirmed_by IS NULL)), 
	CONSTRAINT fk_extracted_facts_document_same_session FOREIGN KEY(document_id, check_session_id) REFERENCES documents (id, check_session_id) ON DELETE CASCADE, 
	CONSTRAINT fk_extracted_facts_check_session_id_check_sessions FOREIGN KEY(check_session_id) REFERENCES check_sessions (id) ON DELETE CASCADE, 
	CONSTRAINT ck_extracted_facts_fact_key CHECK (fact_key IN ('CONTRACT_DATE', 'CONTRACT_PERIOD', 'PAYMENT_AMOUNT', 'PAYMENT_METHOD', 'TOTAL_SESSIONS', 'USED_SESSIONS', 'CANCEL_REQUEST_DATE', 'BUSINESS_NAME', 'BUSINESS_RESPONSE', 'REFUND_CLAUSE', 'OTHER')), 
	CONSTRAINT ck_extracted_facts_source_kind CHECK (source_kind IN ('DOCUMENT', 'STATEMENT', 'UNKNOWN')), 
	CONSTRAINT ck_extracted_facts_status CHECK (status IN ('EXTRACTED', 'CONFIRMED', 'MODIFIED', 'REJECTED')), 
	CONSTRAINT fk_extracted_facts_confirmed_by_users FOREIGN KEY(confirmed_by) REFERENCES users (id)
);
CREATE INDEX ix_extracted_facts_check_session_id_fact_key ON extracted_facts (check_session_id, fact_key);
COMMENT ON TABLE extracted_facts IS '추출 사실: 1차 추론이 추출한 사실·근거 위치와 사용자 확인 결과(ai_value_text 에 AI 원래 값 보존)';
COMMENT ON COLUMN extracted_facts.id IS '식별자: UUID v4 기본키(순번 노출 방지)';
COMMENT ON COLUMN extracted_facts.check_session_id IS '점검 세션 ID: check_sessions.id';
COMMENT ON COLUMN extracted_facts.fact_key IS '사실 항목: 필수 8항목 등(CONTRACT_DATE, PAYMENT_METHOD …)';
COMMENT ON COLUMN extracted_facts.value_text IS '값(원문): 사용자에게 보이는 값. UNKNOWN 이면 NULL';
COMMENT ON COLUMN extracted_facts.value_normalized IS '정규화 값: {"date":"2026-03-02"}, {"amount":720000,"currency":"KRW"} 등';
COMMENT ON COLUMN extracted_facts.source_kind IS '근거 유형: DOCUMENT(원문)/STATEMENT(진술)/UNKNOWN(미확인)';
COMMENT ON COLUMN extracted_facts.document_id IS '출처 문서 ID: 근거가 문서일 때 필수';
COMMENT ON COLUMN extracted_facts.page_no IS '페이지: 출처 페이지 번호(1부터)';
COMMENT ON COLUMN extracted_facts.offset_start IS '시작 오프셋: 원문 문자 위치(0부터)';
COMMENT ON COLUMN extracted_facts.offset_end IS '끝 오프셋: 원문 문자 위치(시작 이상)';
COMMENT ON COLUMN extracted_facts.source_span IS '출처 구절: 근거가 된 원문 일부';
COMMENT ON COLUMN extracted_facts.status IS '확인 상태: EXTRACTED/CONFIRMED/MODIFIED/REJECTED (CONFIRMED·MODIFIED = 승인된 사실)';
COMMENT ON COLUMN extracted_facts.has_conflict IS '충돌 여부: 같은 항목에 다른 값이 있으면 true';
COMMENT ON COLUMN extracted_facts.ai_value_text IS 'AI 추출값: AI 가 처음 추출한 값(수정 후에도 변경하지 않음, AI-01). UNKNOWN 이면 NULL';
COMMENT ON COLUMN extracted_facts.confirmed_by IS '확인자 ID: users.id, 마지막으로 확인·수정·거부한 사용자';
COMMENT ON COLUMN extracted_facts.confirmed_at IS '확인일시: 마지막 확인 처리 시각. 처리 이력은 audit_logs(UPDATE)';
COMMENT ON COLUMN extracted_facts.created_at IS '생성일시: 행 생성 시각(UTC 저장, 화면은 KST)';
COMMENT ON COLUMN extracted_facts.updated_at IS '수정일시: 마지막 변경 시각';


-- v0.8 G2: 승인·대체된 점검 보고서 본문 불변
CREATE OR REPLACE FUNCTION forbid_approved_report_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('APPROVED', 'SUPERSEDED') AND (
           NEW.summary_text IS DISTINCT FROM OLD.summary_text
        OR NEW.items::text IS DISTINCT FROM OLD.items::text
        OR NEW.version IS DISTINCT FROM OLD.version
        OR NEW.check_session_id IS DISTINCT FROM OLD.check_session_id
        OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
        OR NOT (NEW.status = OLD.status OR (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED'))
    ) THEN
        RAISE EXCEPTION 'approved report % is immutable', OLD.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_inspection_reports_immutable ON inspection_reports;
CREATE TRIGGER trg_inspection_reports_immutable
    BEFORE UPDATE ON inspection_reports
    FOR EACH ROW EXECUTE FUNCTION forbid_approved_report_change();
