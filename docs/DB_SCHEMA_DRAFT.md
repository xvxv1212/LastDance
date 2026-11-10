# DB 스키마 제안 초안

- 상태: **초안 / DB 담당 및 팀 미승인**
- 실제 PostgreSQL 테이블 생성, SQLAlchemy 모델, 마이그레이션은 **아직 미구현**
- AI가 추출한 사실 후보와 사용자가 승인한 사실을 분리하는 것이 핵심 설계 원칙

| 제안 테이블 | 책임 | 주요 컬럼 예시 (미확정) |
| :-- | :-- | :-- |
| `cases` | 상담 준비 사례 식별 | `id`, `status`, `created_at`, `updated_at` |
| `contract_documents` | PDF 메타데이터/사용 여부 | `id`, `case_id`, `filename`, `sha256`, `page_count`, `storage_policy` |
| `fact_candidates` | 원문·진술에서 추출된 **미확인** 사실 | `id`, `case_id`, `field_name`, `candidate_value`, `source_ref`, `status` |
| `fact_reviews` | 사용자의 확인·정정·거절 기록 | `id`, `candidate_id`, `action`, `corrected_value`, `reviewed_at` |
| `verified_snapshots` | 특정 시점에 승인한 사실 묶음(불변 버전) | `id`, `case_id`, `version`, `fact_payload`, `approved_at` |
| `official_sources` | 공식 근거 메타데이터 | `id`, `source_url`, `issuer`, `effective_date`, `checked_at`, `verification_status` |
| `report_versions` | 승인 스냅샷 기반 보고서 이력 | `id`, `case_id`, `snapshot_id`, `report_payload`, `created_at` |
| `audit_events` | 주요 상태 변경·승인 이벤트 | `id`, `case_id`, `event_type`, `event_time`, `actor_type` |

## 설계 검토 질문

- 인증·계정 도입 여부는 미정. `actor_type`만으로 실제 승인자 인증이 된다는 뜻이 아님.
- 원본 PDF와 추출 텍스트의 저장 여부, 암호화, 보존기간, 삭제 요청 대응은 팀 결정이 필요.
- `source_ref`는 페이지 번호/본문 위치/사용자 진술 등을 구별할 수 있어야 함.
- 법령·기관 자료는 적용 범위·시행일·검증 시점을 구분해야 함.
- `verified_snapshots`는 추출 후보가 자동 복사되는 곳이 아니라 **확인·승인 후 생성**되는 버전.
- 외래 키/인덱스/개인정보 보호·권한 모델은 DB 담당자가 이후 설계.
