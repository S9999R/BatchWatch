"""GS1 DataMatrix parsing: raw scanner output and the bracketed form printed under the code."""
from batchwatch.gs1 import parse_gs1

GS = "\x1d"


def test_raw_code_with_group_separators():
    code = "]d2" + "0105012748615328" + "17290228" + "10487069" + GS + "21ABC123XYZ"
    assert parse_gs1(code) == {"gtin": "05012748615328", "expiry": "2029-02-28", "batch": "487069", "serial": "ABC123XYZ"}


def test_variable_field_last_needs_no_separator():
    assert parse_gs1("010628012345678917270600" + "10BF25008690") == {
        "gtin": "06280123456789", "expiry": "2027-06-30", "batch": "BF25008690"}  # day 00 = end of month


def test_bracketed_human_readable_form():
    assert parse_gs1("(01)05012748615328(17)290228(10)487069(21)X1") == {
        "gtin": "05012748615328", "expiry": "2029-02-28", "batch": "487069", "serial": "X1"}


def test_serial_before_batch_and_production_date():
    code = "0105012748615328" + "21SN998" + GS + "11250101" + "10LOT-7A"
    assert parse_gs1(code) == {"gtin": "05012748615328", "serial": "SN998", "production": "2025-01-01", "batch": "LOT-7A"}


def test_not_a_gs1_code():
    assert parse_gs1("https://example.com") == {}
    assert parse_gs1("") == {}
