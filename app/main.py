"""FastAPI entrypoint — API + QueryGuard web UI."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db.connection import ping
from app.llm.ollama_client import OllamaClient
from app.pipeline import run_pipeline, run_sql_pipeline
from app.services.examples import example_questions
from app.services.history import query_history
from app.services.schema_cache import schema_cache

WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(
    title="QueryGuard — Text-to-SQL with Guardrails",
    description="Local Text-to-SQL via Ollama with SQL guardrails and hallucination detection",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    execute: bool = True
    skip_judge: bool = False


class SqlRequest(BaseModel):
    sql: str = Field(..., min_length=1)
    question: str = "(manual SQL)"
    execute: bool = True
    skip_judge: bool = True


class QueryResponse(BaseModel):
    question: str
    status: str
    sql: str | None = None
    raw_sql: str | None = None
    generation: dict[str, Any] | None = None
    guardrails: dict[str, Any] | None = None
    schema_check: dict[str, Any] | None = None
    judge: dict[str, Any] | None = None
    explain: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    block_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = None
    timings: dict[str, int] = Field(default_factory=dict)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    history_id: str | None = None


def _require_db() -> None:
    if not get_settings().database_url:
        raise HTTPException(status_code=400, detail="DATABASE_URL is not configured")


def _record(result_dict: dict[str, Any]) -> QueryResponse:
    entry = query_history.add(result_dict)
    result_dict["history_id"] = entry.id
    return QueryResponse(**result_dict)


@app.get("/api/health")
@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    db_ok = False
    db_error = None
    try:
        if settings.database_url:
            db_ok = ping()
    except Exception as exc:
        db_error = str(exc)

    ollama = OllamaClient(timeout_sec=8)
    ollama_ok = False
    models: list[str] = []
    try:
        ollama_ok = ollama.is_available()
        if ollama_ok:
            models = ollama.list_models()
    except Exception:
        ollama_ok = False

    return {
        "status": "ok" if db_ok and ollama_ok else "degraded",
        "database": {"ok": db_ok, "error": db_error},
        "ollama": {
            "ok": ollama_ok,
            "host": settings.ollama.host,
            "models": models,
            "sql_model": settings.ollama.sql_model,
            "judge_model": settings.ollama.judge_model,
        },
        "safety": {
            "max_rows": settings.safety.max_rows,
            "judge_mode": settings.safety.judge_mode,
            "table_allowlist": settings.safety.table_allowlist,
        },
        "schema_cache": schema_cache.meta(),
    }


@app.get("/api/schema")
@app.get("/schema")
def schema(refresh: bool = False) -> dict[str, Any]:
    _require_db()
    try:
        graph = schema_cache.get(force_refresh=refresh)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    tables = []
    for name, info in sorted(graph.tables.items()):
        tables.append(
            {
                "name": name,
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "nullable": c.nullable,
                        "primary_key": c.primary_key,
                    }
                    for c in info.columns
                ],
                "foreign_keys": [
                    {
                        "columns": fk.constrained_columns,
                        "referred_table": fk.referred_table,
                        "referred_columns": fk.referred_columns,
                    }
                    for fk in info.foreign_keys
                ],
            }
        )
    return {
        "dialect": graph.dialect,
        "tables": tables,
        "cache": schema_cache.meta(),
    }


@app.post("/api/schema/refresh")
def schema_refresh() -> dict[str, Any]:
    _require_db()
    schema_cache.invalidate()
    return schema(refresh=True)


@app.get("/api/examples")
def examples() -> dict[str, Any]:
    _require_db()
    graph = schema_cache.get()
    return {"examples": example_questions(graph)}


@app.get("/api/history")
def history(limit: int = 20) -> dict[str, Any]:
    return {"items": query_history.list(limit=limit)}


@app.delete("/api/history")
def history_clear() -> dict[str, str]:
    query_history.clear()
    return {"status": "cleared"}


@app.post("/api/query", response_model=QueryResponse)
@app.post("/query", response_model=QueryResponse)
def query(body: QueryRequest) -> QueryResponse:
    _require_db()
    result = run_pipeline(
        body.question,
        execute=body.execute,
        skip_judge=body.skip_judge,
    )
    return _record(result.to_dict())


@app.post("/api/sql", response_model=QueryResponse)
def run_sql(body: SqlRequest) -> QueryResponse:
    _require_db()
    result = run_sql_pipeline(
        body.sql,
        question=body.question,
        execute=body.execute,
        skip_judge=body.skip_judge,
    )
    return _record(result.to_dict())


@app.post("/api/export.csv")
def export_csv(body: QueryRequest) -> StreamingResponse:
    """Run a question and stream results as CSV (executed queries only)."""
    _require_db()
    result = run_pipeline(body.question, execute=True, skip_judge=body.skip_judge)
    query_history.add(result.to_dict())
    if result.status != "executed" or not result.result or not result.result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Query did not produce exportable rows",
                "status": result.status,
                "block_reasons": result.block_reasons,
            },
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    cols = result.result.get("columns") or []
    writer.writerow(cols)
    for row in result.result.get("rows") or []:
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=queryguard_export.csv"},
    )


@app.get("/")
def index() -> FileResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Web UI not found")
    return FileResponse(index_path)


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
