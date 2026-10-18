"""Daily spending guard for the paid actions of the public demo.

Only starting a new cross-border trace costs money (Nemotron tokens plus Tavily credits). Cached traces, stock
checks, search and barcode lookups are free and are never limited. Counters reset at midnight UTC and live in
memory, so a restart resets them too; that errs toward allowing a few extra runs, never toward blocking forever.

Limits come from the environment so they can be changed without touching code:
  TRACE_DAILY_LIMIT        new traces per day for the whole site (default 30)
  TRACE_PER_VISITOR_LIMIT  new traces per day for one visitor, keyed by IP address (default 5)
"""
import os
import threading
from datetime import datetime, timezone


class DailyLimiter:
    def __init__(self, total: int, per_visitor: int):
        self.total, self.per_visitor = total, per_visitor
        self._day = None
        self._used_total = 0
        self._used_by: dict[str, int] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "DailyLimiter":
        return cls(int(os.environ.get("TRACE_DAILY_LIMIT", "30")), int(os.environ.get("TRACE_PER_VISITOR_LIMIT", "5")))

    def _roll(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self._used_total, self._used_by = today, 0, {}

    def take(self, visitor: str) -> str | None:
        """Reserve one run. Returns None if allowed, or the reason it is not."""
        with self._lock:
            self._roll()
            if self._used_total >= self.total:
                return (f"The demo has reached today's limit of {self.total} new traces. Traces that already ran stay "
                        "available, and the limit resets at midnight UTC.")
            if self._used_by.get(visitor, 0) >= self.per_visitor:
                return f"You have started {self.per_visitor} new traces today, the limit per visitor. Please try again tomorrow."
            self._used_total += 1
            self._used_by[visitor] = self._used_by.get(visitor, 0) + 1
            return None

    def give_back(self, visitor: str) -> None:
        """Return a reserved run that failed before spending anything (for example, missing API keys)."""
        with self._lock:
            self._used_total = max(0, self._used_total - 1)
            if self._used_by.get(visitor):
                self._used_by[visitor] -= 1

    def status(self) -> dict:
        with self._lock:
            self._roll()
            return {"daily_limit": self.total, "used_today": self._used_total, "per_visitor_limit": self.per_visitor}
