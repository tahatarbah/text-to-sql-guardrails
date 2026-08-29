"""Unit tests for schema hallucination detection."""

from __future__ import annotations

from app.db.schema import ColumnInfo, ForeignKeyInfo, SchemaGraph, TableInfo
from app.hallucination.schema_check import check_against_schema
from app.llm.generator import extract_sql


def _sample_schema() -> SchemaGraph:
    customers = TableInfo(
        name="customers",
        schema="public",
        columns=[
            ColumnInfo("id", "INTEGER", nullable=False, primary_key=True),
            ColumnInfo("name", "VARCHAR", nullable=False),
            ColumnInfo("email", "VARCHAR"),
        ],
    )
    orders = TableInfo(
        name="orders",
        schema="public",
        columns=[
            ColumnInfo("id", "INTEGER", nullable=False, primary_key=True),
            ColumnInfo("customer_id", "INTEGER", nullable=False),
            ColumnInfo("amount", "NUMERIC"),
        ],
        foreign_keys=[
            ForeignKeyInfo(["customer_id"], "customers", ["id"]),
        ],
    )
    return SchemaGraph(
        tables={
            customers.qualified_name: customers,
            orders.qualified_name: orders,
        },
        dialect="postgresql",
    )


def test_valid_query_passes():
    schema = _sample_schema()
    result = check_against_schema(
        "SELECT id, name FROM customers LIMIT 10",
        schema,
    )
    assert result.ok
    assert result.confidence == 1.0
    assert "customers" in result.referenced_tables or any(
        "customers" in t for t in result.referenced_tables
    )


def test_unknown_table_flagged():
    schema = _sample_schema()
    result = check_against_schema(
        "SELECT * FROM spaceships LIMIT 5",
        schema,
    )
    assert not result.ok
    assert any(i.kind == "unknown_table" for i in result.issues)
    assert result.confidence < 1.0


def test_unknown_column_flagged():
    schema = _sample_schema()
    result = check_against_schema(
        "SELECT id, telepathy FROM customers LIMIT 5",
        schema,
    )
    assert not result.ok
    assert any(i.kind == "unknown_column" for i in result.issues)


def test_join_with_known_columns_ok():
    schema = _sample_schema()
    sql = """
    SELECT c.name, o.amount
    FROM customers c
    JOIN orders o ON o.customer_id = c.id
    LIMIT 10
    """
    result = check_against_schema(sql, schema)
    assert result.ok


def test_extract_sql_from_fence():
    raw = "Sure! Here you go:\n```sql\nSELECT 1;\n```\nHope that helps."
    assert extract_sql(raw).upper().startswith("SELECT")


def test_extract_sql_plain():
    assert extract_sql("SELECT id FROM customers").startswith("SELECT")
