"""In-memory schema cache with TTL and forced refresh."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.db.connection import get_engine
from app.db.schema import SchemaGraph, introspect_schema

_DEFAULT_TTL_SEC = 300


@dataclass
class CachedSchema:
    graph: SchemaGraph
    loaded_at: float
    ttl_sec: int = _DEFAULT_TTL_SEC

    @property
    def age_sec(self) -> float:
        return time.time() - self.loaded_at

    @property
    def expired(self) -> bool:
        return self.age_sec > self.ttl_sec


class SchemaCache:
    def __init__(self, ttl_sec: int = _DEFAULT_TTL_SEC) -> None:
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._cached: CachedSchema | None = None

    def get(self, *, force_refresh: bool = False) -> SchemaGraph:
        with self._lock:
            if (
                not force_refresh
                and self._cached is not None
                and not self._cached.expired
            ):
                return self._cached.graph
            graph = introspect_schema(get_engine())
            self._cached = CachedSchema(graph=graph, loaded_at=time.time(), ttl_sec=self._ttl)
            return graph

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def meta(self) -> dict:
        with self._lock:
            if self._cached is None:
                return {"cached": False, "table_count": 0, "age_sec": None, "ttl_sec": self._ttl}
            return {
                "cached": True,
                "table_count": len(self._cached.graph.tables),
                "age_sec": round(self._cached.age_sec, 1),
                "ttl_sec": self._ttl,
                "dialect": self._cached.graph.dialect,
            }


schema_cache = SchemaCache()
