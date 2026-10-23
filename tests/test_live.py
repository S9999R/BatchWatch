"""The live search opens every SFDA notice for a batch no stored alert names, and says what it could and could not read."""
import contextlib

import pytest

from batchwatch import live
from batchwatch import sources as S

NOTICES = {
    "SFDA-19096": "Product name: BUSULCAN 6 mg/ml Vial\nBatch number: 656089\nRegistration number: 2412246484",
    "SFDA-19190": "Voluntary Recall of batch 2509984 from the product",
    "SFDA-19000": "Recall of batches 008 and A-12/34 of a vial",
}


@pytest.fixture
def sfda(monkeypatch):
    fetched = []

    class Client:
        pass

    def detail(c, item):
        fetched.append(item["alert_id"])
        if NOTICES.get(item["alert_id"]) == "boom":
            raise RuntimeError("page down")
        return {**item, "title": "t", "date": "2026-06-04", "text": NOTICES[item["alert_id"]]}

    monkeypatch.setattr(S, "_client", lambda: contextlib.nullcontext(Client()))
    monkeypatch.setattr(S, "list_sfda", lambda c, limit: [{"alert_id": k, "url": f"https://sfda/{k}"} for k in NOTICES])
    monkeypatch.setattr(S, "detail_sfda", detail)
    monkeypatch.setattr(live, "_listing", (0.0, []))
    monkeypatch.setattr(live, "_notices", {})
    return fetched


def test_finds_a_notice_the_registry_has_not_read(sfda):
    r = live.search_sfda("656089", stored={})
    assert r["status"] == "found" and r["complete"] and r["searched"] == r["listed"] == 3
    hit = r["hits"][0]
    assert hit["alert_id"] == "SFDA-19096" and not hit["in_registry"] and "656089" in hit["quote"]


def test_not_found_says_how_much_was_searched(sfda):
    r = live.search_sfda("999999", stored={})
    assert r["status"] == "not_found" and r["hits"] == [] and r["complete"] and r["searched"] == 3


def test_registry_text_is_searched_without_downloading(sfda):
    r = live.search_sfda("656089", stored={"SFDA-19096": NOTICES["SFDA-19096"]})
    assert r["hits"][0]["in_registry"] and "SFDA-19096" not in sfda and r["from_registry"] == 1


def test_short_batch_and_spacing_still_match_but_a_substring_does_not(sfda):
    assert live.search_sfda("008", stored={})["status"] == "found"
    assert live.search_sfda("a 12-34", stored={})["status"] == "found"
    assert live.search_sfda("65608", stored={})["status"] == "not_found"  # part of 656089 is not 656089


def test_an_unreadable_notice_makes_the_answer_incomplete(sfda):
    NOTICES["SFDA-19190"] = "boom"
    try:
        r = live.search_sfda("999999", stored={})
    finally:
        NOTICES["SFDA-19190"] = "Voluntary Recall of batch 2509984 from the product"
    assert r["status"] == "not_found" and r["failed"] == 1 and not r["complete"]


def test_a_batch_too_short_to_search_is_skipped(sfda):
    assert live.search_sfda("12", stored={})["status"] == "skipped"
