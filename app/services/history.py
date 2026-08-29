"""In-memory query history ring buffer."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class HistoryEntry:
    id: str
    ts: float
    question: str
    status: str
    sql: str | None = None
    confidence: float | None = None
    row_count: int | None = None
    block_reasons: list[str] = field(default_factory=list)
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QueryHistory:
    def __init__(self, maxlen: int = 50) -> None:
        self._items: deque[HistoryEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, result: dict[str, Any]) -> HistoryEntry:
        rows = None
        if result.get("result") and result["result"].get("ok"):
            rows = result["result"].get("row_count")
        entry = HistoryEntry(
            id=str(uuid.uuid4())[:8],
            ts=time.time(),
            question=result.get("question") or "",
            status=result.get("status") or "unknown",
            sql=result.get("sql"),
            confidence=result.get("confidence"),
            row_count=rows,
            block_reasons=list(result.get("block_reasons") or []),
            duration_ms=(result.get("timings") or {}).get("total_ms"),
        )
        with self._lock:
            self._items.appendleft(entry)
        return entry

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in list(self._items)[:limit]]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


query_history = QueryHistory()
