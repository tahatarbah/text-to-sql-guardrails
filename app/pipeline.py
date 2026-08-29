"""End-to-end Text-to-SQL pipeline."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import get_settings
from app.db.executor import ExecuteResult, ExplainResult, execute_readonly, explain_query
from app.db.schema import SchemaGraph
from app.guardrails.sql_validator import GuardrailResult, validate_sql
from app.hallucination.judge import JudgeResult, judge_sql
from app.hallucination.schema_check import SchemaCheckResult, check_against_schema
from app.llm.generator import GenerationResult, generate_sql
from app.llm.ollama_client import OllamaClient
from app.services.schema_cache import schema_cache


@dataclass
class PipelineResult:
    question: str
    status: str  # executed | blocked | validated
    sql: str | None = None
    raw_sql: str | None = None
    generation: dict[str, Any] | None = None
    guardrails: dict[str, Any] | None = None
    schema_check: dict[str, Any] | None = None
    judge: dict[str, Any] | None = None
    explain: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    block_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None
    timings: dict[str, int] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _guard_to_dict(g: GuardrailResult) -> dict[str, Any]:
    return {
        "ok": g.ok,
        "sql": g.sql,
        "dialect": g.dialect,
        "issues": [
            {"code": i.code, "message": i.message, "severity": i.severity} for i in g.issues
        ],
    }


def _schema_to_dict(s: SchemaCheckResult) -> dict[str, Any]:
    return {
        "ok": s.ok,
        "confidence": s.confidence,
        "referenced_tables": s.referenced_tables,
        "issues": [
            {
                "kind": i.kind,
                "message": i.message,
                "table": i.table,
                "column": i.column,
            }
            for i in s.issues
        ],
    }


def _judge_to_dict(j: JudgeResult) -> dict[str, Any]:
    return {
        "enabled": j.enabled,
        "faithful": j.faithful,
        "score": j.score,
        "issues": j.issues,
        "error": j.error,
        "blocked": j.blocked,
    }


def _explain_to_dict(e: ExplainResult) -> dict[str, Any]:
    return {"ok": e.ok, "error": e.error, "plan": e.plan}


def _exec_to_dict(e: ExecuteResult) -> dict[str, Any]:
    return {
        "ok": e.ok,
        "columns": e.columns,
        "rows": e.rows,
        "row_count": e.row_count,
        "error": e.error,
    }


def _stage(name: str, ok: bool, detail: str = "", ms: int = 0) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail, "ms": ms}


def _finalize(
    result: PipelineResult,
    t0: float,
    timings: dict[str, int],
) -> PipelineResult:
    timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
    result.timings = timings
    return result


def _validate_and_maybe_execute(
    *,
    question: str,
    sql_source: str,
    graph: SchemaGraph,
    execute: bool,
    ollama: OllamaClient,
    gen_dict: dict[str, Any] | None,
    prompt_tables: list[str] | None,
    skip_judge: bool,
    t0: float,
    timings: dict[str, int],
    stages: list[dict[str, Any]],
) -> PipelineResult:
    settings = get_settings()
    block_reasons: list[str] = []
    warnings: list[str] = []

    t_guard = time.perf_counter()
    guard = validate_sql(
        sql_source,
        dialect=graph.dialect,
        max_rows=settings.safety.max_rows,
        table_allowlist=settings.safety.table_allowlist or None,
    )
    timings["guardrails_ms"] = int((time.perf_counter() - t_guard) * 1000)
    for issue in guard.issues:
        if issue.severity == "warning":
            warnings.append(issue.message)
    if not guard.ok:
        block_reasons.extend(i.message for i in guard.errors)
    stages.append(
        _stage(
            "guardrails",
            guard.ok,
            "passed" if guard.ok else "; ".join(block_reasons) or "failed",
            timings["guardrails_ms"],
        )
    )

    t_schema = time.perf_counter()
    schema_check = check_against_schema(guard.sql, graph, dialect=graph.dialect)
    timings["schema_check_ms"] = int((time.perf_counter() - t_schema) * 1000)
    if not schema_check.ok:
        block_reasons.extend(i.message for i in schema_check.issues)
    stages.append(
        _stage(
            "schema_check",
            schema_check.ok,
            f"confidence={schema_check.confidence:.2f}",
            timings["schema_check_ms"],
        )
    )

    judge: JudgeResult
    if skip_judge:
        judge = JudgeResult(enabled=False)
        timings["judge_ms"] = 0
        stages.append(_stage("judge", True, "skipped", 0))
    else:
        t_judge = time.perf_counter()
        judge = judge_sql(
            question,
            guard.sql,
            graph,
            prompt_tables,
            client=ollama,
        )
        timings["judge_ms"] = int((time.perf_counter() - t_judge) * 1000)
        if judge.blocked:
            block_reasons.append(
                f"Faithfulness judge blocked query (score={judge.score:.2f}): "
                + ("; ".join(judge.issues) if judge.issues else "below threshold")
            )
        elif judge.enabled and (
            not judge.faithful or judge.score < settings.safety.judge_threshold
        ):
            warnings.append(
                f"Judge warning (score={judge.score:.2f}): "
                + ("; ".join(judge.issues) if judge.issues else "below threshold")
            )
        stages.append(
            _stage(
                "judge",
                not judge.blocked,
                f"score={judge.score:.2f}" if judge.enabled else "off",
                timings["judge_ms"],
            )
        )

    confidence = schema_check.confidence
    if judge.enabled:
        confidence = (confidence + judge.score) / 2.0

    base_kwargs = dict(
        question=question,
        sql=guard.sql,
        raw_sql=sql_source,
        generation=gen_dict,
        guardrails=_guard_to_dict(guard),
        schema_check=_schema_to_dict(schema_check),
        judge=_judge_to_dict(judge),
        block_reasons=block_reasons,
        warnings=warnings,
        confidence=confidence,
        stages=stages,
    )

    if block_reasons:
        return _finalize(
            PipelineResult(status="blocked", **base_kwargs),
            t0,
            timings,
        )

    t_explain = time.perf_counter()
    explain = explain_query(
        guard.sql,
        timeout_sec=settings.safety.query_timeout_sec,
    )
    timings["explain_ms"] = int((time.perf_counter() - t_explain) * 1000)
    stages.append(
        _stage(
            "explain",
            explain.ok,
            "ok" if explain.ok else (explain.error or "failed"),
            timings["explain_ms"],
        )
    )
    if not explain.ok:
        return _finalize(
            PipelineResult(
                question=question,
                status="blocked",
                sql=guard.sql,
                raw_sql=sql_source,
                generation=gen_dict,
                guardrails=_guard_to_dict(guard),
                schema_check=_schema_to_dict(schema_check),
                judge=_judge_to_dict(judge),
                explain=_explain_to_dict(explain),
                block_reasons=[f"EXPLAIN failed: {explain.error}"],
                warnings=warnings,
                confidence=confidence,
                stages=stages,
            ),
            t0,
            timings,
        )

    if not execute:
        return _finalize(
            PipelineResult(
                question=question,
                status="validated",
                sql=guard.sql,
                raw_sql=sql_source,
                generation=gen_dict,
                guardrails=_guard_to_dict(guard),
                schema_check=_schema_to_dict(schema_check),
                judge=_judge_to_dict(judge),
                explain=_explain_to_dict(explain),
                block_reasons=block_reasons,
                warnings=warnings,
                confidence=confidence,
                stages=stages,
            ),
            t0,
            timings,
        )

    t_exec = time.perf_counter()
    exec_result = execute_readonly(
        guard.sql,
        max_rows=settings.safety.max_rows,
        timeout_sec=settings.safety.query_timeout_sec,
    )
    timings["execute_ms"] = int((time.perf_counter() - t_exec) * 1000)
    stages.append(
        _stage(
            "execute",
            exec_result.ok,
            f"{exec_result.row_count} rows" if exec_result.ok else (exec_result.error or ""),
            timings["execute_ms"],
        )
    )
    if not exec_result.ok:
        return _finalize(
            PipelineResult(
                question=question,
                status="blocked",
                sql=guard.sql,
                raw_sql=sql_source,
                generation=gen_dict,
                guardrails=_guard_to_dict(guard),
                schema_check=_schema_to_dict(schema_check),
                judge=_judge_to_dict(judge),
                explain=_explain_to_dict(explain),
                result=_exec_to_dict(exec_result),
                block_reasons=[f"Execution failed: {exec_result.error}"],
                warnings=warnings,
                confidence=confidence,
                stages=stages,
            ),
            t0,
            timings,
        )

    return _finalize(
        PipelineResult(
            question=question,
            status="executed",
            sql=guard.sql,
            raw_sql=sql_source,
            generation=gen_dict,
            guardrails=_guard_to_dict(guard),
            schema_check=_schema_to_dict(schema_check),
            judge=_judge_to_dict(judge),
            explain=_explain_to_dict(explain),
            result=_exec_to_dict(exec_result),
            block_reasons=block_reasons,
            warnings=warnings,
            confidence=confidence,
            stages=stages,
        ),
        t0,
        timings,
    )


def run_pipeline(
    question: str,
    *,
    execute: bool = True,
    schema: SchemaGraph | None = None,
    client: OllamaClient | None = None,
    skip_judge: bool = False,
) -> PipelineResult:
    t0 = time.perf_counter()
    timings: dict[str, int] = {}
    stages: list[dict[str, Any]] = []
    ollama = client or OllamaClient()

    if not question or not question.strip():
        return _finalize(
            PipelineResult(
                question=question or "",
                status="blocked",
                block_reasons=["Question is empty"],
            ),
            t0,
            timings,
        )

    try:
        t_schema = time.perf_counter()
        graph = schema or schema_cache.get()
        timings["schema_ms"] = int((time.perf_counter() - t_schema) * 1000)
        stages.append(
            _stage("schema", True, f"{len(graph.tables)} tables", timings["schema_ms"])
        )
    except Exception as exc:
        return _finalize(
            PipelineResult(
                question=question,
                status="blocked",
                block_reasons=[f"Schema introspection failed: {exc}"],
                stages=[_stage("schema", False, str(exc))],
            ),
            t0,
            timings,
        )

    t_gen = time.perf_counter()
    gen: GenerationResult = generate_sql(question, graph, client=ollama)
    timings["generate_ms"] = int((time.perf_counter() - t_gen) * 1000)
    gen_dict = {
        "model": gen.model,
        "tables_used_in_prompt": gen.tables_used_in_prompt,
        "raw_response": gen.raw_response,
        "error": gen.error,
    }
    if gen.error or not gen.sql:
        stages.append(_stage("generate", False, gen.error or "empty", timings["generate_ms"]))
        return _finalize(
            PipelineResult(
                question=question,
                status="blocked",
                generation=gen_dict,
                block_reasons=[gen.error or "No SQL generated"],
                stages=stages,
            ),
            t0,
            timings,
        )
    stages.append(
        _stage(
            "generate",
            True,
            f"model={gen.model}",
            timings["generate_ms"],
        )
    )

    return _validate_and_maybe_execute(
        question=question,
        sql_source=gen.sql,
        graph=graph,
        execute=execute,
        ollama=ollama,
        gen_dict=gen_dict,
        prompt_tables=gen.tables_used_in_prompt,
        skip_judge=skip_judge,
        t0=t0,
        timings=timings,
        stages=stages,
    )


def run_sql_pipeline(
    sql: str,
    *,
    question: str = "(manual SQL)",
    execute: bool = True,
    schema: SchemaGraph | None = None,
    client: OllamaClient | None = None,
    skip_judge: bool = True,
) -> PipelineResult:
    """Validate / execute user-edited SQL without regenerating from NL."""
    t0 = time.perf_counter()
    timings: dict[str, int] = {}
    stages: list[dict[str, Any]] = []
    ollama = client or OllamaClient()

    if not sql or not sql.strip():
        return _finalize(
            PipelineResult(
                question=question,
                status="blocked",
                block_reasons=["SQL is empty"],
            ),
            t0,
            timings,
        )

    try:
        t_schema = time.perf_counter()
        graph = schema or schema_cache.get()
        timings["schema_ms"] = int((time.perf_counter() - t_schema) * 1000)
        stages.append(
            _stage("schema", True, f"{len(graph.tables)} tables", timings["schema_ms"])
        )
    except Exception as exc:
        return _finalize(
            PipelineResult(
                question=question,
                status="blocked",
                block_reasons=[f"Schema introspection failed: {exc}"],
                stages=[_stage("schema", False, str(exc))],
            ),
            t0,
            timings,
        )

    timings["generate_ms"] = 0
    stages.append(_stage("generate", True, "manual SQL", 0))

    return _validate_and_maybe_execute(
        question=question,
        sql_source=sql,
        graph=graph,
        execute=execute,
        ollama=ollama,
        gen_dict={
            "model": "manual",
            "tables_used_in_prompt": [],
            "raw_response": sql,
            "error": None,
        },
        prompt_tables=None,
        skip_judge=skip_judge,
        t0=t0,
        timings=timings,
        stages=stages,
    )
