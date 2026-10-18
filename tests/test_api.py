"""The web API the page uses: stock files as pharmacies export them, one-pack lookups, the feed and the watcher status."""
import io

import pytest
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)


def test_riyadh_sample_reads_arabic_columns():
    d = client.get("/api/check/sample?name=riyadh").json()
    assert d["columns"]["batch_number"] == "رقم التشغيلة" and d["columns_by"] == "names"
    assert d["counts"]["CONFIRMED"] >= 8 and d["counts"]["VERIFY"] >= 1
    m = d["matches"][0]
    assert m["alert"]["model"].startswith("Nemotron 3") and m["alert"]["checks_total"] == 6
    assert d["checked_at"] and d["alerts_as_of"]


def test_excel_export_with_unusual_headers():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Item Code", "Description", "Lot No.", "Exp. Date", "On Hand"])
    ws.append(["X1", "MOVICOL Plain 13.7 g sachets", "487069", "2029-02-28", 12])
    buf = io.BytesIO()
    wb.save(buf)
    d = client.post("/api/check", files={"file": ("stock.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    assert d["columns"]["batch_number"] == "Lot No." and d["counts"]["CONFIRMED"] == 1


def test_legacy_arabic_windows_encoding():
    raw = "اسم الصنف,رقم التشغيلة,الكمية\nLorivan 2mg Tab,104173,30\n".encode("cp1256")
    d = client.post("/api/check", files={"file": ("ar.csv", raw, "text/csv")}).json()
    assert d["columns"]["product_name"] == "اسم الصنف" and d["counts"]["CONFIRMED"] == 1


def test_unknown_columns_fail_loudly(monkeypatch):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    r = client.post("/api/check", files={"file": ("x.csv", b"Col A,Col B\nfoo,bar\n", "text/csv")})
    assert r.status_code == 400 and "Col A" in r.json()["detail"]


def test_lookup_shows_the_alert_product_with_its_strength():
    d = client.get("/api/lookup?batch=104173").json()
    m = d["matches"][0]
    assert m["tier"] == "VERIFY" and "2mg" in m["alert"]["product"].replace(" ", "")
    assert client.get("/api/lookup?batch=25JO1305").json()["matches"][0]["tier"] == "VERIFY"


def test_alert_detail_has_the_notice_text_and_six_checks():
    d = client.get("/api/alert/SFDA-19402").json()
    assert "104173" in d["text"] and len(d["alert"]["checks"]) == 6


def test_feed_and_status():
    f = client.get("/feed.xml?source=sfda")
    assert f.status_code == 200 and f.headers["content-type"].startswith("application/atom+xml") and "<entry>" in f.text
    s = client.get("/api/status").json()
    assert {"sources", "last_run", "detection", "alerts_as_of"} <= s.keys() and len(s["sources"]) == 10


@pytest.mark.parametrize("path", ["/api/registry", "/api/insights", "/api/limits", "/"])
def test_pages_load(path):
    assert client.get(path).status_code == 200
