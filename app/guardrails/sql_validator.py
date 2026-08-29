"""SQL guardrails using sqlglot AST validation."""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.guardrails.policy import (
    ALLOWED_STATEMENT_TYPES,
    DANGEROUS_FUNCTIONS,
    DENIED_STATEMENT_TYPES,
)


@dataclass
class GuardrailIssue:
    code: str
    message: str
    severity: str = "error"  # error | warning


@dataclass
class GuardrailResult:
    ok: bool
    sql: str
    issues: list[GuardrailIssue] = field(default_factory=list)
    dialect: str = "postgres"

    @property
    def errors(self) -> list[GuardrailIssue]:
        return [i for i in self.issues if i.severity == "error"]


def _map_dialect(dialect: str) -> str:
    d = (dialect or "postgresql").lower()
    if d in ("postgresql", "postgres", "pg"):
        return "postgres"
    if d in ("mysql", "mariadb"):
        return "mysql"
    return d


def _strip_trailing_semicolons(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _has_multiple_statements(sql: str, dialect: str) -> bool:
    """Detect multi-statement SQL (e.g. SELECT 1; DROP TABLE x)."""
    cleaned = sql.strip()
    # Allow a single trailing semicolon
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1]
    try:
        statements = sqlglot.parse(cleaned, read=dialect)
    except ParseError:
        # Fallback: raw semicolon outside of strings is suspicious
        return ";" in cleaned
    # parse may return [None] for empty
    real = [s for s in statements if s is not None]
    return len(real) > 1


def _statement_type(node: exp.Expression) -> str:
    if isinstance(node, exp.Select):
        return "SELECT"
    if isinstance(node, exp.Union):
        return "SELECT"
    if isinstance(node, exp.Subquery):
        return "SELECT"
    # WITH ... SELECT is typically a Select with CTEs
    if isinstance(node, exp.Command):
        return (node.name or "COMMAND").upper()
    return type(node).__name__.upper()


def _find_limit(select: exp.Select) -> int | None:
    limit = select.args.get("limit")
    if not limit:
        return None
    if isinstance(limit, exp.Limit):
        expr = limit.expression
        if isinstance(expr, exp.Literal) and expr.is_int:
            return int(expr.this)
    return None


def _set_limit(select: exp.Select, value: int) -> None:
    select.set("limit", exp.Limit(expression=exp.Literal.number(value)))


def _collect_tables(node: exp.Expression) -> set[str]:
    tables: set[str] = set()
    for t in node.find_all(exp.Table):
        name = t.name
        if t.db:
            name = f"{t.db}.{name}"
        if name:
            tables.add(name)
    return tables


def _collect_functions(node: exp.Expression) -> set[str]:
    names: set[str] = set()
    for fn in node.find_all(exp.Func):
        # sqlglot Func subclasses often have .sql_name()
        try:
            n = fn.sql_name()
        except Exception:
            n = type(fn).__name__
        if n:
            names.add(n.lower())
    for anon in node.find_all(exp.Anonymous):
        if anon.this:
            names.add(str(anon.this).lower())
    return names


def validate_sql(
    sql: str,
    *,
    dialect: str = "postgresql",
    max_rows: int = 100,
    table_allowlist: list[str] | None = None,
) -> GuardrailResult:
    """Validate and normalize SQL. Fail closed on any hard error."""
    sg_dialect = _map_dialect(dialect)
    issues: list[GuardrailIssue] = []
    original = sql or ""

    if not original.strip():
        return GuardrailResult(
            ok=False,
            sql=original,
            issues=[GuardrailIssue("empty", "SQL is empty")],
            dialect=sg_dialect,
        )

    if _has_multiple_statements(original, sg_dialect):
        return GuardrailResult(
            ok=False,
            sql=original,
            issues=[
                GuardrailIssue(
                    "multi_statement",
                    "Multiple SQL statements are not allowed",
                )
            ],
            dialect=sg_dialect,
        )

    cleaned = _strip_trailing_semicolons(original)

    # Block INTO OUTFILE / DUMPFILE style clauses in raw text (MySQL)
    lower = cleaned.lower()
    for banned in ("into outfile", "into dumpfile", "for update", "for share"):
        if banned in lower:
            issues.append(
                GuardrailIssue("banned_clause", f"Banned clause detected: {banned}")
            )
    if any(i.code == "banned_clause" for i in issues):
        return GuardrailResult(
            ok=False,
            sql=cleaned,
            issues=issues,
            dialect=sg_dialect,
        )

    try:
        parsed = sqlglot.parse_one(cleaned, read=sg_dialect)
    except ParseError as exc:
        return GuardrailResult(
            ok=False,
            sql=cleaned,
            issues=issues
            + [GuardrailIssue("parse_error", f"Failed to parse SQL: {exc}")],
            dialect=sg_dialect,
        )

    if parsed is None:
        return GuardrailResult(
            ok=False,
            sql=cleaned,
            issues=[GuardrailIssue("parse_error", "Failed to parse SQL")],
            dialect=sg_dialect,
        )

    # Reject non-SELECT roots (Insert, Delete, etc.)
    root_type = type(parsed).__name__.upper()
    if root_type in DENIED_STATEMENT_TYPES or (
        root_type not in ALLOWED_STATEMENT_TYPES
        and not isinstance(parsed, (exp.Select, exp.Union, exp.Subquery))
    ):
        # WITH queries parse as Select
        if not isinstance(parsed, (exp.Select, exp.Union)):
            issues.append(
                GuardrailIssue(
                    "denied_statement",
                    f"Statement type '{root_type}' is not allowed; only SELECT/WITH",
                )
            )

    # Walk for dangerous nested statements
    for node in parsed.walk():
        if isinstance(
            node,
            (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Drop,
                exp.Create,
                exp.Alter,
                exp.Grant,
                exp.Command,
            ),
        ):
            issues.append(
                GuardrailIssue(
                    "denied_statement",
                    f"Forbidden statement node: {type(node).__name__}",
                )
            )

    # Dangerous functions
    for fname in _collect_functions(parsed):
        if fname in DANGEROUS_FUNCTIONS:
            issues.append(
                GuardrailIssue(
                    "dangerous_function",
                    f"Dangerous function not allowed: {fname}()",
                )
            )

    # Table allowlist
    allow = {t.lower() for t in (table_allowlist or [])}
    if allow:
        for tname in _collect_tables(parsed):
            simple = tname.split(".")[-1].lower()
            full = tname.lower()
            if simple not in allow and full not in allow:
                issues.append(
                    GuardrailIssue(
                        "allowlist",
                        f"Table '{tname}' is not in the allowlist",
                    )
                )

    # LIMIT inject / cap on top-level SELECT
    normalized = parsed
    if isinstance(parsed, exp.Select):
        current = _find_limit(parsed)
        if current is None:
            _set_limit(parsed, max_rows)
            issues.append(
                GuardrailIssue(
                    "limit_injected",
                    f"Injected LIMIT {max_rows}",
                    severity="warning",
                )
            )
        elif current > max_rows:
            _set_limit(parsed, max_rows)
            issues.append(
                GuardrailIssue(
                    "limit_capped",
                    f"Capped LIMIT from {current} to {max_rows}",
                    severity="warning",
                )
            )
    elif isinstance(parsed, exp.Union):
        # Wrap union in subquery with limit if needed
        wrapped = exp.select("*").from_(parsed.subquery(alias="q")).limit(max_rows)
        normalized = wrapped
        issues.append(
            GuardrailIssue(
                "limit_injected",
                f"Wrapped UNION with LIMIT {max_rows}",
                severity="warning",
            )
        )

    hard_errors = [i for i in issues if i.severity == "error"]
    try:
        out_sql = normalized.sql(dialect=sg_dialect, pretty=True)
    except Exception:
        out_sql = cleaned

    return GuardrailResult(
        ok=len(hard_errors) == 0,
        sql=out_sql,
        issues=issues,
        dialect=sg_dialect,
    )
