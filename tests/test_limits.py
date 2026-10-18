"""The spending guard: only new traces cost money, so only they are counted and capped."""
from fastapi.testclient import TestClient

import app.main as main
from app.limits import DailyLimiter


def test_limiter_caps_total_and_per_visitor():
    lim = DailyLimiter(total=3, per_visitor=2)
    assert lim.take("a") is None and lim.take("a") is None
    assert "per visitor" in lim.take("a")          # third run for the same visitor
    assert lim.take("b") is None
    assert "today's limit" in lim.take("c")        # site-wide cap reached
    lim.give_back("b")
    assert lim.take("c") is None                   # a refunded run can be used again


class FakeStore:
    def __init__(self):
        self.rows = [{"alert_id": "cached-one"}, {"alert_id": "new-1"}, {"alert_id": "new-2"}]
        self.saved = {"cached-one": {"findings": []}}

    def entries(self):
        return self.rows

    def entry(self, alert_id):
        return next((e for e in self.rows if e["alert_id"] == alert_id), None)

    def trace(self, alert_id):
        return self.saved.get(alert_id)

    def forget_trace(self, alert_id):
        pass

    def runs(self, limit=200):
        return []


def test_new_trace_is_capped_but_cached_trace_is_free(monkeypatch):
    runs = []

    def fake_trace(entry, on_step=None):
        on_step({"event": "search", "query": "x"})
        runs.append(entry["alert_id"])
        return {"findings": []}

    monkeypatch.setattr(main, "limiter", DailyLimiter(total=1, per_visitor=1))
    monkeypatch.setattr(main, "store", FakeStore())
    monkeypatch.setattr(main, "trace_cached", fake_trace)
    client = TestClient(main.app)

    for _ in range(3):  # saved results never touch the limit
        r = client.post("/api/trace/cached-one")
        assert r.status_code == 200 and "event: done" in r.text
    r = client.post("/api/trace/new-1")
    assert r.status_code == 200 and "event: step" in r.text and "event: done" in r.text  # the agent's steps stream live
    blocked = client.post("/api/trace/new-2")
    assert blocked.status_code == 429 and "limit" in blocked.json()["detail"]
    assert runs == ["new-1"]
    assert client.get("/api/limits").json()["used_today"] == 1
