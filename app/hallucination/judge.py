"""Optional Ollama faithfulness judge."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_settings
from app.db.schema import SchemaGraph
from app.llm.ollama_client import OllamaClient, OllamaError
from app.llm.prompts import JUDGE_SYSTEM, JUDGE_USER_TEMPLATE


@dataclass
class JudgeResult:
    enabled: bool
    faithful: bool = True
    score: float = 1.0
    issues: list[str] = field(default_factory=list)
    error: str | None = None
    blocked: bool = False


def judge_sql(
    question: str,
    sql: str,
    schema: SchemaGraph,
    schema_prompt_tables: list[str] | None = None,
    *,
    client: OllamaClient | None = None,
) -> JudgeResult:
    settings = get_settings()
    mode = settings.safety.judge_mode
    if mode == "off":
        return JudgeResult(enabled=False)

    ollama = client or OllamaClient()
    schema_text = schema.to_prompt_text(schema_prompt_tables or schema.table_names()[:12])
    prompt = JUDGE_USER_TEMPLATE.format(
        dialect=schema.dialect,
        schema=schema_text,
        question=question.strip(),
        sql=sql.strip(),
    )
    try:
        data = ollama.generate_json(
            settings.ollama.judge_model,
            prompt,
            system=JUDGE_SYSTEM,
            temperature=0.0,
        )
        faithful = bool(data.get("faithful", True))
        score = float(data.get("score", 1.0 if faithful else 0.0))
        score = max(0.0, min(1.0, score))
        issues = [str(x) for x in (data.get("issues") or [])]
        below = score < settings.safety.judge_threshold or not faithful
        blocked = below and mode == "block"
        return JudgeResult(
            enabled=True,
            faithful=faithful and not below,
            score=score,
            issues=issues,
            blocked=blocked,
        )
    except OllamaError as exc:
        # Fail closed when judge is required for blocking mode
        blocked = mode == "block"
        return JudgeResult(
            enabled=True,
            faithful=False,
            score=0.0,
            issues=[f"Judge failed: {exc}"],
            error=str(exc),
            blocked=blocked,
        )
