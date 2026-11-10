"""환경 변수에서 설정을 읽는다. 값은 저장소 루트의 .env 에 둔다."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DB 연결은 DB 모델이 들어올 때부터 사용한다. 비어 있어도 서버는 뜬다.
    database_url: str = ""
    cors_origins: str = "http://localhost:5173"
    max_pdf_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 30

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
