"""Read-only SQL execution and EXPLAIN dry-run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.connection import dialect_name, get_engine


@dataclass
class ExplainResult:
    ok: bool
    plan: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class ExecuteResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    error: str | None = None


def _set_read_only(conn, dialect: str) -> None:
    if dialect == "postgresql":
        conn.execute(text("SET TRANSACTION READ ONLY"))
    elif dialect == "mysql":
        # MySQL: prefer transaction read only when supported
        try:
            conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        except Exception:
            pass


def explain_query(
    sql: str,
    engine: Engine | None = None,
    timeout_sec: int = 30,
) -> ExplainResult:
    eng = engine or get_engine()
    dialect = dialect_name(eng)
    try:
        with eng.connect() as conn:
            trans = conn.begin()
            try:
                _set_read_only(conn, dialect)
                if dialect == "postgresql":
                    conn.execute(
                        text("SET LOCAL statement_timeout = :ms"),
                        {"ms": int(timeout_sec * 1000)},
                    )
                    result = conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))
                    rows = [dict(r._mapping) for r in result]
                else:
                    result = conn.execute(text(f"EXPLAIN {sql}"))
                    rows = [dict(r._mapping) for r in result]
                trans.rollback()
                return ExplainResult(ok=True, plan=rows)
            except Exception as exc:
                trans.rollback()
                return ExplainResult(ok=False, error=str(exc))
    except Exception as exc:
        return ExplainResult(ok=False, error=str(exc))


def execute_readonly(
    sql: str,
    engine: Engine | None = None,
    max_rows: int = 100,
    timeout_sec: int = 30,
) -> ExecuteResult:
    eng = engine or get_engine()
    dialect = dialect_name(eng)
    try:
        with eng.connect() as conn:
            trans = conn.begin()
            try:
                _set_read_only(conn, dialect)
                if dialect == "postgresql":
                    conn.execute(
                        text("SET LOCAL statement_timeout = :ms"),
                        {"ms": int(timeout_sec * 1000)},
                    )
                result = conn.execute(text(sql))
                keys = list(result.keys()) if result.returns_rows else []
                fetched = result.fetchmany(max_rows) if result.returns_rows else []
                rows = [list(r) for r in fetched]
                trans.rollback()  # never commit; read-only safety
                return ExecuteResult(
                    ok=True,
                    columns=keys,
                    rows=rows,
                    row_count=len(rows),
                )
            except Exception as exc:
                trans.rollback()
                return ExecuteResult(ok=False, error=str(exc))
    except Exception as exc:
        return ExecuteResult(ok=False, error=str(exc))
