"""Unit tests for SQL guardrails."""

from __future__ import annotations

from app.guardrails.sql_validator import validate_sql


def test_allows_simple_select_and_injects_limit():
    result = validate_sql("SELECT id, name FROM customers", max_rows=50)
    assert result.ok
    assert "LIMIT 50" in result.sql.upper()
    assert any(i.code == "limit_injected" for i in result.issues)


def test_caps_excessive_limit():
    result = validate_sql("SELECT * FROM customers LIMIT 9999", max_rows=100)
    assert result.ok
    assert "LIMIT 100" in result.sql.upper()
    assert any(i.code == "limit_capped" for i in result.issues)


def test_blocks_drop():
    result = validate_sql("DROP TABLE customers")
    assert not result.ok
    assert any(i.code == "denied_statement" for i in result.errors)


def test_blocks_delete():
    result = validate_sql("DELETE FROM customers WHERE id = 1")
    assert not result.ok


def test_blocks_multi_statement():
    result = validate_sql("SELECT 1; DROP TABLE customers")
    assert not result.ok
    assert any(i.code == "multi_statement" for i in result.errors)


def test_blocks_insert():
    result = validate_sql("INSERT INTO customers (name) VALUES ('x')")
    assert not result.ok


def test_allowlist_blocks_unknown_table():
    result = validate_sql(
        "SELECT id FROM secrets",
        table_allowlist=["customers", "orders"],
        max_rows=10,
    )
    assert not result.ok
    assert any(i.code == "allowlist" for i in result.errors)


def test_allowlist_allows_listed_table():
    result = validate_sql(
        "SELECT id FROM customers",
        table_allowlist=["customers", "orders"],
        max_rows=10,
    )
    assert result.ok


def test_blocks_into_outfile():
    result = validate_sql(
        "SELECT * FROM customers INTO OUTFILE '/tmp/x.csv'",
        dialect="mysql",
    )
    assert not result.ok
    assert any(i.code == "banned_clause" for i in result.errors)


def test_with_cte_allowed():
    sql = """
    WITH recent AS (
      SELECT id FROM orders WHERE created_at > '2024-01-01'
    )
    SELECT * FROM recent
    """
    result = validate_sql(sql, max_rows=25)
    assert result.ok
    assert "LIMIT" in result.sql.upper()


def test_empty_sql():
    result = validate_sql("   ")
    assert not result.ok
    assert any(i.code == "empty" for i in result.errors)
