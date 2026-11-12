"""ORM 공통 기반: Base 클래스, 제약조건 명명 규칙, 공용 컬럼 타입."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import JSON, Enum, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# 제약조건 이름을 규칙적으로 만들어 마이그레이션·장애 분석 시 어떤 제약인지 바로 알 수 있게 한다.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# PostgreSQL 에서는 JSONB(색인·검색 가능), 테스트용 SQLite 에서는 JSON 으로 동작
# none_as_null: 파이썬 None 을 JSON 'null' 이 아니라 SQL NULL 로 저장한다
# → "IS NULL" CHECK(스냅샷 완결성 등)가 JSON 컬럼에서도 의도대로 동작한다.
JsonType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def code_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """코드값 컬럼 타입.

    DB 고유 ENUM 타입 대신 VARCHAR + CHECK 제약으로 만든다.
    → 코드값 추가 시 'ALTER TYPE' 없이 CHECK 만 바꾸면 되어 운영 변경이 쉽다.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=30,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


# v0.6: 임베딩(vector) 타입은 업무 DB 에서 사라졌다.
# 벡터는 벡터 지식 DB 가 보관한다(app/infrastructure/knowledge.py).
