"""Application configuration from env and config.yaml."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class OllamaSettings(BaseModel):
    host: str = "http://localhost:11434"
    sql_model: str = "sqlcoder:7b"
    judge_model: str = "llama3.2"
    temperature: float = 0.1
    timeout_sec: int = 120


class SafetySettings(BaseModel):
    max_rows: int = 100
    query_timeout_sec: int = 30
    table_allowlist: list[str] = Field(default_factory=list)
    judge_mode: Literal["block", "warn", "off"] = "block"
    judge_threshold: float = 0.7
    schema_top_n: int = 12


class ApiSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class Settings(BaseModel):
    database_url: str = ""
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    api: ApiSettings = Field(default_factory=ApiSettings)


def _load_yaml() -> dict:
    path = ROOT / "config.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _split_allowlist(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    raw = _load_yaml()
    ollama_raw = raw.get("ollama") or {}
    safety_raw = raw.get("safety") or {}
    api_raw = raw.get("api") or {}

    allowlist = safety_raw.get("table_allowlist") or []
    env_allowlist = _split_allowlist(os.getenv("TABLE_ALLOWLIST"))
    if env_allowlist:
        allowlist = env_allowlist

    return Settings(
        database_url=os.getenv("DATABASE_URL", "").strip(),
        ollama=OllamaSettings(
            host=os.getenv("OLLAMA_HOST", ollama_raw.get("host", "http://localhost:11434")),
            sql_model=os.getenv("SQL_MODEL", ollama_raw.get("sql_model", "sqlcoder:7b")),
            judge_model=os.getenv("JUDGE_MODEL", ollama_raw.get("judge_model", "llama3.2")),
            temperature=float(ollama_raw.get("temperature", 0.1)),
            timeout_sec=int(ollama_raw.get("timeout_sec", 120)),
        ),
        safety=SafetySettings(
            max_rows=int(os.getenv("MAX_ROWS", safety_raw.get("max_rows", 100))),
            query_timeout_sec=int(
                os.getenv("QUERY_TIMEOUT_SEC", safety_raw.get("query_timeout_sec", 30))
            ),
            table_allowlist=list(allowlist),
            judge_mode=os.getenv(  # type: ignore[arg-type]
                "JUDGE_MODE", safety_raw.get("judge_mode", "block")
            ),
            judge_threshold=float(
                os.getenv("JUDGE_THRESHOLD", safety_raw.get("judge_threshold", 0.7))
            ),
            schema_top_n=int(safety_raw.get("schema_top_n", 12)),
        ),
        api=ApiSettings(
            host=api_raw.get("host", "0.0.0.0"),
            port=int(api_raw.get("port", 8000)),
        ),
    )
