"""Where the web app reads its data: the local registry, or the live "data" branch the scheduler publishes.

Locally everything comes from <data dir> (registry/ by default). When REGISTRY_BASE_URL is set (for example
https://raw.githubusercontent.com/<owner>/<repo>/data), the live registry, run log and watcher state are fetched
from there every few minutes, so the deployed site shows new alerts without being redeployed. Cross-border
traces are read from registry/traces (shipped with the code) and, when missing there, from the same URL.
"""
import json
import os
import threading
import time

import httpx

from batchwatch.pipeline import read_registry, registry_path
from batchwatch.trace import TRACES, cached_trace, trace_path
from batchwatch.watch import load_state, read_runs

REFRESH_SECONDS = 300


class Store:
    def __init__(self) -> None:
        self.base = os.environ.get("REGISTRY_BASE_URL", "").rstrip("/")
        self._lock = threading.Lock()
        self._local = {"mtime": None, "entries": []}
        self._remote: dict[str, dict] = {}  # file -> {"at", "etag", "data"}
        self._traces: dict[str, tuple] = {}  # alert_id -> (mtime, trace or None)

    # ---------------------------------------------------------------- remote files
    def _fetch(self, name: str, parse):
        """A file from the data branch, refreshed at most every REFRESH_SECONDS; the last good copy on failure."""
        cached = self._remote.get(name)
        if cached and time.time() - cached["at"] < REFRESH_SECONDS:
            return cached["data"]
        try:
            headers = {"If-None-Match": cached["etag"]} if cached and cached.get("etag") else {}
            r = httpx.get(f"{self.base}/{name}", headers=headers, timeout=20, follow_redirects=True)
            if r.status_code == 304 and cached:
                cached["at"] = time.time()
                return cached["data"]
            r.raise_for_status()
            data = parse(r.text)
            self._remote[name] = {"at": time.time(), "etag": r.headers.get("etag"), "data": data}
            return data
        except Exception:
            if cached:
                cached["at"] = time.time()  # keep serving the last good copy; retry after the next interval
                return cached["data"]
            self._remote[name] = {"at": time.time(), "etag": None, "data": None}  # a missing file is asked for again later, not on every request
            return None

    # ---------------------------------------------------------------- registry
    def entries(self) -> list[dict]:
        if self.base:
            with self._lock:
                rows = self._fetch("alerts.jsonl", lambda t: [json.loads(l) for l in t.splitlines() if l.strip()])
            if rows:
                return rows
        path = registry_path()
        mtime = path.stat().st_mtime if path.exists() else None
        if mtime != self._local["mtime"]:
            self._local["entries"], self._local["mtime"] = read_registry(), mtime
        return self._local["entries"]

    def entry(self, alert_id: str) -> dict | None:
        return next((e for e in self.entries() if e.get("alert_id") == alert_id), None)

    def runs(self, limit: int = 200) -> list[dict]:
        if self.base:
            with self._lock:
                rows = self._fetch("runs.jsonl", lambda t: [json.loads(l) for l in t.splitlines() if l.strip()])
            return (rows or [])[-limit:]
        return read_runs(limit)

    def state(self) -> dict:
        if self.base:
            with self._lock:
                return self._fetch("watch_state.json", json.loads) or {}
        return load_state()

    # ---------------------------------------------------------------- traces
    def trace(self, alert_id: str) -> dict | None:
        path = trace_path(alert_id)
        mtime = path.stat().st_mtime if path.exists() else None
        hit = self._traces.get(alert_id)
        if hit and hit[0] == mtime and (mtime or not self.base):
            return hit[1]
        result = cached_trace(alert_id)
        if result is None and self.base:
            with self._lock:
                result = self._fetch(f"traces/{path.name}", json.loads)
        self._traces[alert_id] = (mtime, result)
        return result

    def forget_trace(self, alert_id: str) -> None:
        self._traces.pop(alert_id, None)


store = Store()
TRACES.mkdir(parents=True, exist_ok=True)
