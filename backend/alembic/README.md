# Alembic — 아직 초기화하지 않음

이 디렉터리는 DB 스키마 변경 이력을 보관할 **위치만 확보**한 것입니다.
`alembic.ini`, `env.py`, `versions/` 마이그레이션 파일은 아직 없습니다.

팀 DB 스키마 승인 후 `alembic init alembic`으로 설정을 만들고
`backend/app/db/`의 SQLAlchemy 모델과 연결하세요.
개발 DB 비밀번호는 `.env`에만 보관합니다.
