"""ORM 메타데이터 → 테이블명세서용 JSON(db/table_spec.json). 문서와 코드의 불일치를 원천 차단한다."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from app.infrastructure.db import models  # noqa: F401
from app.infrastructure.db.base import Base

ORDER = [m.__tablename__ for m in Base.__subclasses__()]  # models.py 선언 순서 = 문서 순서


def main() -> None:
    dialect = postgresql.dialect()
    tables = []
    for name in ORDER:
        t = Base.metadata.tables[name]
        cols = []
        for c in t.columns:
            keys = []
            if c.primary_key:
                keys.append("PK")
            # 단일 컬럼 FK(예: users.id)를 먼저 보여 주고, 복합 FK(소유자 일치 등)는 '제약조건' 표에서 다룬다
            fks = sorted(c.foreign_keys, key=lambda f: len(f.constraint.columns) if f.constraint else 1)
            if fks:
                keys.append("FK")
            if c.unique:
                keys.append("UQ")
            default = None
            if c.server_default is not None:
                default = str(
                    c.server_default.arg.text
                    if hasattr(c.server_default.arg, "text")
                    else c.server_default.arg
                )
            cols.append(
                {
                    "name": c.name,
                    "ko": c.info.get("ko", ""),
                    "type": c.type.compile(dialect=dialect),
                    "nullable": bool(c.nullable),
                    "keys": keys,
                    "fk": f"{fks[0].column.table.name}.{fks[0].column.name}" if fks else None,
                    "on_delete": fks[0].ondelete if fks else None,
                    "default": default,
                    "comment": c.comment or "",
                }
            )
        constraints = []
        # 코드값(Enum) 컬럼이 자동 생성한 CHECK(ck_테이블_컬럼)는 '코드 정의' 장에서 다루므로 제외
        enum_checks = {f"ck_{name}_{c.name}" for c in t.columns}
        for con in t.constraints:
            if isinstance(con, UniqueConstraint) and len(con.columns) > 1:
                constraints.append(
                    {
                        "kind": "UNIQUE",
                        "name": str(con.name),
                        "detail": ", ".join(c.name for c in con.columns),
                    }
                )
            elif isinstance(con, ForeignKeyConstraint) and len(con.columns) > 1:
                # 복합 FK = '같은 소유자'·'같은 사건' 규칙(DB-03). 컬럼 표에는 단일 FK 만 보임
                fk_cols = ", ".join(c.name for c in con.columns)
                ref = ", ".join(f"{e.column.table.name}.{e.column.name}" for e in con.elements)
                on_del = f" (삭제 시 {con.ondelete})" if con.ondelete else ""
                constraints.append(
                    {"kind": "FK(복합)", "name": str(con.name), "detail": f"({fk_cols}) → ({ref}){on_del}"}
                )
            elif isinstance(con, CheckConstraint) and str(con.name) not in enum_checks:
                constraints.append({"kind": "CHECK", "name": str(con.name), "detail": str(con.sqltext)})
        indexes = [
            {"name": str(i.name), "detail": ", ".join(c.name for c in i.columns)}
            for i in sorted(t.indexes, key=lambda i: str(i.name))
        ]
        tables.append(
            {
                "name": name,
                "ko": t.info["ko"],
                "comment": t.comment,
                "columns": cols,
                "constraints": constraints,
                "indexes": indexes,
            }
        )
    Path("db/table_spec.json").write_text(json.dumps(tables, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(tables), sum(len(t["columns"]) for t in tables))


if __name__ == "__main__":
    main()
