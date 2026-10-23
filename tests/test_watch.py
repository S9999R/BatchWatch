"""The smart update reads only new alerts, respects its limits and never loses an alert it could not read."""
import json
import os
import shutil
import tempfile

import pytest

from batchwatch import watch
from batchwatch.notify import atom_feed, describe


@pytest.fixture
def data_dir(monkeypatch):
    d = tempfile.mkdtemp(prefix="bw-watch-")
    monkeypatch.setenv("BATCHWATCH_DATA_DIR", d)
    monkeypatch.setenv("WATCH_MAX_NEW_PER_RUN", "25")
    monkeypatch.setenv("WATCH_DAILY_USD", "0.5")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def fake_sources(monkeypatch, listing: dict, details: list):
    """Two fake regulators: 'a' lists full alerts, 'b' lists ids and needs a detail fetch."""
    def lister(name):
        def list_new(c, limit, state):
            if listing.get(name) == "boom":
                raise RuntimeError("regulator down")
            return [dict(i) for i in listing.get(name, [])]
        return list_new

    def detail(c, item):
        details.append(item["alert_id"])
        return {**item, "text": f"Recall of batch {item['alert_id']}"}

    monkeypatch.setattr(watch, "SOURCES", {
        "a": watch.Source("Reg A", lister("a")),
        "b": watch.Source("Reg B", lister("b"), detail),
    })


def fake_extract(alert, meter):
    meter.cost_usd += 0.001
    return {"record": {"alert_type": "recall", "products": [{"product_name": "X", "batches": [{"batch_number": alert["alert_id"]}]}]},
            "model_used": "nano", "error": None}


def alert(i, source="Reg A"):
    return {"source": source, "alert_id": i, "url": f"https://reg.example/{i}", "date": "2026-10-10", "title": f"Alert {i}",
            "text": f"Recall of batch {i}"}


def test_only_new_alerts_are_read_and_logged(monkeypatch, data_dir):
    details = []
    fake_sources(monkeypatch, {"a": [alert("A1"), alert("A2")], "b": [{"source": "Reg B", "alert_id": "B1", "url": "u", "date": "2026-10-10"}]}, details)
    run = watch.run_once(extract=fake_extract, notify=lambda entries: ["test"])
    assert {n["alert_id"] for n in run["new"]} == {"A1", "A2", "B1"}
    assert details == ["B1"]  # the detail page is fetched only for the new alert
    assert all(n["backfill"] for n in run["new"])  # first look at a source: published before we watched

    # Second run: nothing new, so nothing is fetched or extracted, and the run still counts as a check.
    calls = []
    run2 = watch.run_once(extract=lambda a, m: calls.append(a) or fake_extract(a, m), notify=lambda e: [])
    assert run2["new"] == [] and calls == [] and details == ["B1"]
    assert [s["status"] for s in run2["sources"]] == ["unchanged", "unchanged"]
    assert len(watch.read_runs()) == 2

    # A third alert appears: it is read, and it is no longer a backfill.
    fake_sources(monkeypatch, {"a": [alert("A3"), alert("A1")], "b": []}, details)
    run3 = watch.run_once(extract=fake_extract, notify=lambda e: [])
    assert [n["alert_id"] for n in run3["new"]] == ["A3"] and run3["new"][0]["backfill"] is False
    rows = [json.loads(l) for l in open(os.path.join(data_dir, "alerts.jsonl"), encoding="utf-8")]
    assert {"A1", "A2", "A3", "B1"} <= {r["alert_id"] for r in rows}
    assert next(r for r in rows if r["alert_id"] == "A3")["text"].startswith("Recall of batch A3")


def test_limits_defer_instead_of_dropping(monkeypatch, data_dir):
    monkeypatch.setenv("WATCH_MAX_NEW_PER_RUN", "1")
    fake_sources(monkeypatch, {"a": [alert("A1"), alert("A2")]}, [])
    run = watch.run_once(extract=fake_extract, notify=lambda e: [])
    assert len(run["new"]) == 1 and run["deferred"] == 1
    run2 = watch.run_once(extract=fake_extract, notify=lambda e: [])
    assert len(run2["new"]) == 1  # the deferred alert is picked up next time


def test_a_failing_source_or_alert_does_not_stop_the_run(monkeypatch, data_dir):
    fake_sources(monkeypatch, {"a": "boom", "b": [{"source": "Reg B", "alert_id": "B1", "url": "u", "date": "2026-10-10"}]}, [])
    run = watch.run_once(extract=lambda a, m: {"error": "malformed reply"}, notify=lambda e: [])
    assert run["sources"][0]["status"] == "error" and "regulator down" in run["sources"][0]["error"]
    assert run["new"] == [] and run["failed"][0]["alert_id"] == "B1"
    run2 = watch.run_once(extract=fake_extract, notify=lambda e: [])
    assert [n["alert_id"] for n in run2["new"]] == ["B1"]  # retried, not lost


def test_dry_run_changes_nothing(monkeypatch, data_dir):
    fake_sources(monkeypatch, {"a": [alert("A1")]}, [])
    run = watch.run_once(dry_run=True, extract=lambda a, m: pytest.fail("no model call in a dry run"))
    assert run["sources"][0]["new_ids"] == ["A1"]
    assert not os.path.exists(os.path.join(data_dir, "watch_state.json"))
    assert not os.path.exists(os.path.join(data_dir, "runs.jsonl"))


def test_validators_wait_until_every_new_alert_is_read(monkeypatch, data_dir):
    """A 304 must never hide an alert that was left for the next run."""
    class Resp:
        status_code, headers, text = 200, {"etag": '"v2"'}, ""
        def raise_for_status(self):
            pass

    state = {}
    monkeypatch.setattr(watch, "SOURCES", {"a": watch.Source("Reg A", lambda c, limit, st: (
        watch._conditional_get(type("C", (), {"get": lambda self, url, headers=None, **kw: Resp()})(), "u", st),
        [alert("A1"), alert("A2")])[1])})
    monkeypatch.setenv("WATCH_MAX_NEW_PER_RUN", "1")
    watch.run_once(extract=fake_extract, notify=lambda e: [])
    assert "etag" not in watch.load_state()["a"]  # one alert deferred: keep asking for the full listing
    watch.run_once(extract=fake_extract, notify=lambda e: [])
    assert watch.load_state()["a"]["etag"] == '"v2"'


def test_messages_and_feed():
    entry = {"alert_id": "SFDA-1", "source": "SFDA (Saudi Arabia)", "url": "https://sfda/1", "published_at": "2026-10-04",
             "detected_at": "2026-10-04T08:00:00+00:00",
             "record": {"alert_type": "recall", "reason": "Out of specification", "required_action": "Stop dispensing",
                        "products": [{"product_name": "Lorivan 2mg Tab", "batches": [{"batch_number": "104173"}, {"batch_number": "ALL"}]}]}}
    d = describe(entry)
    assert d["stop"] and d["headline"] == "Recall · SFDA (Saudi Arabia)" and d["batches"] == "104173, all batches"
    feed = atom_feed([entry], "https://bw.example")
    assert "<feed" in feed and "Lorivan 2mg Tab" in feed and "urn:batchwatch:SFDA-1" in feed
    assert "Lorivan" not in atom_feed([entry], country_filter=lambda e: False)


def test_sfda_listing_picks_newest_notice_numbers_even_when_the_list_is_not_in_date_order():
    from batchwatch import sources

    pages = {0: [18858, 19769], 1: [19190, 19700], 2: [19096, 17786]}  # 19096 (a June recall) is not on page 0

    class Client:
        def get(self, url, params):
            html = "".join(f'<a href="/en/drugs_circulars_withdrawal/{i}">x</a>' for i in pages.get(params["page"], []))
            return type("Resp", (), {"text": html, "raise_for_status": lambda self: None})()

    ids = [i["alert_id"] for i in sources.list_sfda(Client(), 4)]
    assert ids == ["SFDA-19769", "SFDA-19700", "SFDA-19190", "SFDA-19096"]


def test_sfda_listing_fails_loudly_when_a_page_errors_instead_of_looking_like_the_end():
    from batchwatch import sources

    class Client:
        def get(self, url, params):
            def boom():
                raise RuntimeError("HTTP 503")
            return type("Resp", (), {"text": "", "raise_for_status": lambda self: boom()})()

    with pytest.raises(RuntimeError):
        sources.list_sfda(Client(), 4)
