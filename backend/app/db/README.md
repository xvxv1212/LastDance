# DB 레이어 — 설계 대기

DB 담당: 김교민. 현재 `docker-compose.yml`로 개발용 PostgreSQL 컨테이너를 켤 수 있으나
**SQLAlchemy 모델, 연결 세션, Alembic 마이그레이션은 구현되지 않았습니다.**

1. `docs/DB_SCHEMA_DRAFT.md`를 바탕으로 팀의 데이터 보관·승인 정책을 합의합니다.
2. `models.py`, `session.py`를 추가한 뒤 Alembic을 실제로 초기화합니다.
3. 테스트용 DB와 운영용 DB를 분리하고 개인 식별 정보가 포함된 원본은 Git에 넣지 않습니다.

**주의:** PDF 추출 API는 현재 DB를 사용하지 않습니다. `/health`가 정상이어도 DB 접속 확인은 아닙니다.
