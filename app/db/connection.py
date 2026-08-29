"""SQLAlchemy engine helpers."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings


def _connect_args(url: str) -> dict:
    lower = url.lower()
    if lower.startswith("postgresql") or "+psycopg" in lower:
        return {"connect_timeout": 5}
    if "mysql" in lower:
        return {"connect_timeout": 5}
    return {}


@lru_cache
def get_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    url = (database_url or settings.database_url).strip()
    if not url:
        raise ValueError(
            "DATABASE_URL is not set. Copy .env.example to .env and set a Postgres or MySQL URI."
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        pool_timeout=5,
        connect_args=_connect_args(url),
    )


def dialect_name(engine: Engine | None = None) -> str:
    eng = engine or get_engine()
    return eng.dialect.name  # postgresql | mysql | sqlite | ...


def ping(engine: Engine | None = None) -> bool:
    eng = engine or get_engine()
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
