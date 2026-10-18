"""Offline tests for the checks that decide whether an agent finding is shown as verified."""
from batchwatch.trace import _batch_on_page, _quote_found, add_timeline, parse_date

PAGE = """Tablet Nitras | Glycerin Trinitrate 2.6 mg (Reg # 056007) | 24L069 | M/s Caraway Pharmaceuticals.
The Sample is declared as "sub-standard" on the basis of Assay & Dissolution Test. Loratadine 24 | batch 24 | Murfy."""


def test_quote_must_come_from_the_page():
    assert _quote_found('The Sample is declared as "sub-standard" on the basis of Assay & Dissolution Test.', PAGE)
    assert _quote_found("THE SAMPLE IS  DECLARED as \"sub-standard\" on the basis of assay", PAGE)  # case and spacing
    assert not _quote_found("The product was found to be falsified and must be destroyed immediately.", PAGE)
    assert not _quote_found("short", PAGE)


def test_batch_must_be_on_the_page_as_a_whole_identifier():
    assert _batch_on_page("24L069", PAGE)
    assert _batch_on_page("24", PAGE)
    assert not _batch_on_page("4L06", PAGE)
    assert not _batch_on_page("ACH479", PAGE)


def test_dates_and_timeline():
    assert parse_date("14 April, 2026").date().isoformat() == "2026-04-14"
    assert parse_date("2026-08-21").month == 8
    assert parse_date("20260930").day == 30
    assert parse_date("3rd July 2026").day == 3
    assert parse_date("sometime last year") is None
    findings = [{"date": "14 April, 2026"}, {"date": None}]
    add_timeline(findings, "2026-08-18")
    assert findings[0]["days_from_original"] == -126 and findings[0]["date_iso"] == "2026-04-14"
    assert findings[1]["days_from_original"] is None
