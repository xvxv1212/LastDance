"""ORM 모델에서 PostgreSQL DDL(db/schema.sql)을 생성한다.

테이블명세서와 DDL 의 원본은 models.py 하나뿐이다.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from app.infrastructure.db import models  # 모델 등록 + 트리거 DDL
from app.infrastructure.db.base import Base


def main() -> None:
    dialect = postgresql.dialect()
    out = [
        "-- 권리채비 AI 업무 DB PostgreSQL 스키마 (models.py 에서 자동 생성, 직접 수정 금지)",
        "-- v0.6: 공식 지식(벡터)은 별도 벡터 지식 DB → pgvector 확장 불필요",
        "",
    ]
    deferred = []
    for table in Base.metadata.sorted_tables:
        out.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for idx in sorted(table.indexes, key=lambda i: str(i.name)):
            out.append(str(CreateIndex(idx).compile(dialect=dialect)).strip() + ";")
        for fk in table.foreign_key_constraints:
            if fk.use_alter:
                deferred.append(str(AddConstraint(fk).compile(dialect=dialect)).strip() + ";")
        # 테이블·컬럼 주석(논리명/설명)
        out.append(f"COMMENT ON TABLE {table.name} IS '{table.info['ko']}: {table.comment}';")
        for col in table.columns:
            desc = f"{col.info.get('ko', col.name)}: {col.comment}".replace("'", "''")
            out.append(f"COMMENT ON COLUMN {table.name}.{col.name} IS '{desc}';")
        out.append("")
    out += deferred
    # v0.8 G2: 승인 보고서 불변 트리거(models.REPORT_IMMUTABLE_DDL 과 같은 내용)
    out += ["", "-- v0.8 G2: 승인·대체된 점검 보고서 본문 불변", models.REPORT_IMMUTABLE_DDL]
    Path("db/schema.sql").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"tables={len(Base.metadata.sorted_tables)}")


if __name__ == "__main__":
    main()
