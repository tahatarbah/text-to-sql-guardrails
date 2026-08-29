"""Schema introspection → SchemaGraph."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.db.connection import get_engine


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False


@dataclass
class ForeignKeyInfo:
    constrained_columns: list[str]
    referred_table: str
    referred_columns: list[str]


@dataclass
class TableInfo:
    name: str
    schema: str | None
    columns: list[ColumnInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        if self.schema and self.schema not in ("public", "dbo"):
            return f"{self.schema}.{self.name}"
        return self.name


@dataclass
class SchemaGraph:
    tables: dict[str, TableInfo] = field(default_factory=dict)
    dialect: str = "postgresql"

    def table_names(self) -> list[str]:
        return sorted(self.tables.keys())

    def get_table(self, name: str) -> TableInfo | None:
        key = name.lower()
        for tname, info in self.tables.items():
            if tname.lower() == key or info.name.lower() == key:
                return info
            if info.qualified_name.lower() == key:
                return info
        return None

    def has_table(self, name: str) -> bool:
        return self.get_table(name) is not None

    def has_column(self, table: str, column: str) -> bool:
        info = self.get_table(table)
        if not info:
            return False
        col = column.lower()
        return any(c.name.lower() == col for c in info.columns)

    def column_names(self, table: str) -> list[str]:
        info = self.get_table(table)
        if not info:
            return []
        return [c.name for c in info.columns]

    def to_prompt_text(self, table_filter: list[str] | None = None) -> str:
        names = table_filter or self.table_names()
        blocks: list[str] = []
        for name in names:
            info = self.get_table(name)
            if not info:
                continue
            cols = []
            for c in info.columns:
                flags = []
                if c.primary_key:
                    flags.append("PK")
                if not c.nullable:
                    flags.append("NOT NULL")
                suffix = f" ({', '.join(flags)})" if flags else ""
                cols.append(f"  - {c.name}: {c.type}{suffix}")
            fk_lines = []
            for fk in info.foreign_keys:
                fk_lines.append(
                    f"  FK {', '.join(fk.constrained_columns)} -> "
                    f"{fk.referred_table}({', '.join(fk.referred_columns)})"
                )
            body = "\n".join(cols + fk_lines)
            blocks.append(f"TABLE {info.qualified_name}:\n{body}")
        return "\n\n".join(blocks)


_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def select_relevant_tables(
    schema: SchemaGraph, question: str, top_n: int = 12
) -> list[str]:
    """Keyword overlap ranking; embedding-free for fully local use."""
    tokens = {t.lower() for t in _TOKEN_RE.findall(question)}
    scored: list[tuple[float, str]] = []
    for name, info in schema.tables.items():
        score = 0.0
        name_l = info.name.lower()
        if name_l in tokens:
            score += 5.0
        for tok in tokens:
            if tok in name_l or name_l in tok:
                score += 2.0
        for col in info.columns:
            cl = col.name.lower()
            if cl in tokens:
                score += 1.5
            for tok in tokens:
                if len(tok) > 3 and (tok in cl or cl in tok):
                    score += 0.5
        scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = [name for s, name in scored if s > 0][:top_n]
    if not chosen:
        # Fallback: first N tables alphabetically
        return schema.table_names()[:top_n]
    # Always include high-score neighbors; pad if short
    if len(chosen) < min(top_n, len(schema.tables)):
        for _, name in scored:
            if name not in chosen:
                chosen.append(name)
            if len(chosen) >= top_n:
                break
    return chosen


def introspect_schema(engine: Engine | None = None) -> SchemaGraph:
    eng = engine or get_engine()
    insp = inspect(eng)
    dialect = eng.dialect.name
    graph = SchemaGraph(dialect=dialect)

    # Prefer default schema; include all non-system schemas for Postgres
    if dialect == "postgresql":
        schemas = [
            s
            for s in insp.get_schema_names()
            if s not in ("information_schema", "pg_catalog", "pg_toast")
        ]
        if not schemas:
            schemas = ["public"]
    elif dialect == "mysql":
        schemas = [None]
    else:
        schemas = [None]

    for sch in schemas:
        try:
            table_names = insp.get_table_names(schema=sch)
        except Exception:
            continue
        for tname in table_names:
            try:
                cols_raw = insp.get_columns(tname, schema=sch)
                pk = set(insp.get_pk_constraint(tname, schema=sch).get("constrained_columns") or [])
                fks_raw = insp.get_foreign_keys(tname, schema=sch)
            except Exception:
                continue
            columns = [
                ColumnInfo(
                    name=c["name"],
                    type=str(c.get("type", "")),
                    nullable=bool(c.get("nullable", True)),
                    primary_key=c["name"] in pk,
                )
                for c in cols_raw
            ]
            fks = [
                ForeignKeyInfo(
                    constrained_columns=list(fk.get("constrained_columns") or []),
                    referred_table=fk.get("referred_table") or "",
                    referred_columns=list(fk.get("referred_columns") or []),
                )
                for fk in fks_raw
            ]
            info = TableInfo(name=tname, schema=sch, columns=columns, foreign_keys=fks)
            graph.tables[info.qualified_name] = info
    return graph
