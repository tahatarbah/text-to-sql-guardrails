"""Example natural-language questions derived from schema."""

from __future__ import annotations

from app.db.schema import SchemaGraph

_GENERIC = [
    "How many rows are in each table?",
    "List the first 10 rows from the largest-looking table",
]


def example_questions(schema: SchemaGraph) -> list[str]:
    names = {t.lower(): t for t in schema.table_names()}
    examples: list[str] = []

    if "customers" in names:
        examples.append("List all customers and their cities")
        examples.append("How many customers are in each city?")
    if "products" in names:
        examples.append("Show products ordered by price descending")
        examples.append("What is the average product price by category?")
    if "orders" in names and "customers" in names:
        examples.append("Which customers placed the most orders?")
        examples.append("Show completed orders from the last year with customer names")
    if "order_items" in names and "products" in names:
        examples.append("What are the top products by total quantity sold?")
        examples.append("Calculate revenue per product (quantity × unit_price)")

    if not examples:
        # Schema-aware fallbacks from first few tables
        for tname in schema.table_names()[:3]:
            info = schema.tables[tname]
            cols = [c.name for c in info.columns[:3]]
            col_list = ", ".join(cols) if cols else "*"
            examples.append(f"Show {col_list} from {info.name} limited to 10 rows")
            if any(c.primary_key for c in info.columns):
                examples.append(f"Count the number of rows in {info.name}")

    examples.extend(_GENERIC)
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for q in examples:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:10]
