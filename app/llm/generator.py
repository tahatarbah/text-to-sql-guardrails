"""Schema-grounded SQL generation via Ollama."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings
from app.db.schema import SchemaGraph, select_relevant_tables
from app.llm.ollama_client import OllamaClient, OllamaError
from app.llm.prompts import SQL_SYSTEM, SQL_USER_TEMPLATE


_FENCE_RE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)


@dataclass
class GenerationResult:
    sql: str
    raw_response: str
    tables_used_in_prompt: list[str]
    model: str
    error: str | None = None


def extract_sql(text: str) -> str:
    """Pull SQL out of model output (strips markdown fences if present)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    m = _FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    # Drop leading commentary lines until a SQL keyword
    lines = raw.splitlines()
    start = 0
    for i, line in enumerate(lines):
        upper = line.strip().upper()
        if upper.startswith(("SELECT", "WITH", "EXPLAIN")):
            start = i
            break
    sql = "\n".join(lines[start:]).strip()
    # Drop trailing explanation after blank line following SQL
    parts = re.split(r"\n\s*\n", sql, maxsplit=1)
    return parts[0].strip().rstrip(";")


def generate_sql(
    question: str,
    schema: SchemaGraph,
    *,
    client: OllamaClient | None = None,
    model: str | None = None,
) -> GenerationResult:
    settings = get_settings()
    ollama = client or OllamaClient()
    model_name = model or settings.ollama.sql_model
    tables = select_relevant_tables(
        schema, question, top_n=settings.safety.schema_top_n
    )
    schema_text = schema.to_prompt_text(tables)
    prompt = SQL_USER_TEMPLATE.format(
        dialect=schema.dialect,
        schema=schema_text,
        question=question.strip(),
    )
    try:
        raw = ollama.generate(model_name, prompt, system=SQL_SYSTEM)
        sql = extract_sql(raw)
        if not sql:
            return GenerationResult(
                sql="",
                raw_response=raw,
                tables_used_in_prompt=tables,
                model=model_name,
                error="Model returned empty SQL",
            )
        return GenerationResult(
            sql=sql,
            raw_response=raw,
            tables_used_in_prompt=tables,
            model=model_name,
        )
    except OllamaError as exc:
        return GenerationResult(
            sql="",
            raw_response="",
            tables_used_in_prompt=tables,
            model=model_name,
            error=str(exc),
        )
