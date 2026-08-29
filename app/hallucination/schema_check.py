"""Deterministic schema identifier checks against SchemaGraph."""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.db.schema import SchemaGraph


@dataclass
class SchemaIssue:
    kind: str  # unknown_table | unknown_column
    message: str
    table: str | None = None
    column: str | None = None


@dataclass
class SchemaCheckResult:
    ok: bool
    issues: list[SchemaIssue] = field(default_factory=list)
    referenced_tables: list[str] = field(default_factory=list)
    referenced_columns: list[tuple[str | None, str]] = field(default_factory=list)
    confidence: float = 1.0


def _map_dialect(dialect: str) -> str:
    d = (dialect or "postgresql").lower()
    if d in ("postgresql", "postgres", "pg"):
        return "postgres"
    if d in ("mysql", "mariadb"):
        return "mysql"
    return d


def _alias_map(node: exp.Expression) -> dict[str, str]:
    """Map alias -> real table name."""
    mapping: dict[str, str] = {}
    for table in node.find_all(exp.Table):
        real = table.name
        if table.db:
            real = f"{table.db}.{table.name}"
        alias = table.alias_or_name
        if alias:
            mapping[alias.lower()] = real
        if table.name:
            mapping[table.name.lower()] = real
    return mapping


def check_against_schema(
    sql: str,
    schema: SchemaGraph,
    *,
    dialect: str | None = None,
) -> SchemaCheckResult:
    sg_dialect = _map_dialect(dialect or schema.dialect)
    issues: list[SchemaIssue] = []
    try:
        parsed = sqlglot.parse_one(sql, read=sg_dialect)
    except ParseError as exc:
        return SchemaCheckResult(
            ok=False,
            issues=[SchemaIssue("parse_error", f"Cannot parse SQL for schema check: {exc}")],
            confidence=0.0,
        )

    if parsed is None:
        return SchemaCheckResult(
            ok=False,
            issues=[SchemaIssue("parse_error", "Cannot parse SQL for schema check")],
            confidence=0.0,
        )

    aliases = _alias_map(parsed)
    referenced_tables: list[str] = []
    for table in parsed.find_all(exp.Table):
        name = table.name
        if table.db:
            name = f"{table.db}.{table.name}"
        if not name:
            continue
        referenced_tables.append(name)
        if not schema.has_table(name) and not schema.has_table(table.name):
            issues.append(
                SchemaIssue(
                    "unknown_table",
                    f"Unknown table: {name}",
                    table=name,
                )
            )

    referenced_columns: list[tuple[str | None, str]] = []
    for col in parsed.find_all(exp.Column):
        col_name = col.name
        if not col_name or col_name == "*":
            continue
        table_ref = col.table
        real_table: str | None = None
        if table_ref:
            real_table = aliases.get(table_ref.lower(), table_ref)
            referenced_columns.append((real_table, col_name))
            # Resolve table
            if schema.has_table(real_table) or schema.has_table(table_ref):
                tkey = real_table if schema.has_table(real_table) else table_ref
                if not schema.has_column(tkey, col_name):
                    issues.append(
                        SchemaIssue(
                            "unknown_column",
                            f"Unknown column: {tkey}.{col_name}",
                            table=tkey,
                            column=col_name,
                        )
                    )
            else:
                # Table already flagged; still note column
                issues.append(
                    SchemaIssue(
                        "unknown_column",
                        f"Column on unknown table: {table_ref}.{col_name}",
                        table=table_ref,
                        column=col_name,
                    )
                )
        else:
            referenced_columns.append((None, col_name))
            # Unqualified: exists on any referenced table?
            known_tables = [
                t
                for t in referenced_tables
                if schema.has_table(t) or schema.has_table(t.split(".")[-1])
            ]
            if known_tables:
                found = any(schema.has_column(t, col_name) for t in known_tables)
                # Also try simple names
                if not found:
                    found = any(
                        schema.has_column(t.split(".")[-1], col_name) for t in known_tables
                    )
                if not found:
                    # Ambiguous miss — only flag if no table has it at all in schema
                    anywhere = any(
                        schema.has_column(t, col_name) for t in schema.table_names()
                    )
                    if not anywhere:
                        issues.append(
                            SchemaIssue(
                                "unknown_column",
                                f"Unknown column: {col_name}",
                                column=col_name,
                            )
                        )

    # Confidence: start 1.0, -0.35 per unknown table, -0.2 per unknown column
    confidence = 1.0
    for issue in issues:
        if issue.kind == "unknown_table":
            confidence -= 0.35
        elif issue.kind == "unknown_column":
            confidence -= 0.2
        else:
            confidence -= 0.5
    confidence = max(0.0, min(1.0, confidence))

    return SchemaCheckResult(
        ok=len(issues) == 0,
        issues=issues,
        referenced_tables=sorted(set(referenced_tables)),
        referenced_columns=referenced_columns,
        confidence=confidence,
    )
