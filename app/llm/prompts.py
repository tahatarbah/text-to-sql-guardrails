"""Prompt templates for SQL generation and faithfulness judging."""

from __future__ import annotations

SQL_SYSTEM = """You are a careful SQL expert. Generate a single read-only SQL query.
Rules:
- Only SELECT or WITH ... SELECT statements.
- Use ONLY tables and columns listed in the schema.
- Do not invent tables, columns, or values.
- Prefer explicit JOINs using foreign keys when available.
- Always include a LIMIT clause (default 100 unless the question asks otherwise).
- Return ONLY the SQL query — no markdown fences, no commentary."""

SQL_USER_TEMPLATE = """Database dialect: {dialect}

Schema:
{schema}

Question:
{question}

SQL:"""

JUDGE_SYSTEM = """You evaluate whether a SQL query is faithful to a natural-language question
and the provided database schema. Respond with JSON only."""

JUDGE_USER_TEMPLATE = """Dialect: {dialect}

Schema:
{schema}

Question:
{question}

SQL:
{sql}

Return JSON with this shape:
{{
  "faithful": true or false,
  "score": number between 0 and 1,
  "issues": ["list of short problems, empty if none"]
}}

Mark faithful=false if the SQL references unknown tables/columns, changes data,
or clearly fails to answer the question."""
