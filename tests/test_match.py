"""Matching tests: every line of examples/inventory_sample.csv has a known expected outcome.

The alerts come from tests/fixtures/extracted_alerts.jsonl: 45 real regulator alerts extracted by Nemotron and
scored 181/181 against hand labels, frozen so these tests run offline.
"""
import json
from pathlib import Path

import pytest

from batchwatch.match import (CONFIRMED, VERIFY, WITHDRAWN, AlertBatch, compare, confusable_key, load_inventory,
                              norm_header, parse_inventory_columns,
                              match_inventory, norm_batch, norm_gtin, overlap, parse_month, strengths)

ROOT = Path(__file__).resolve().parent.parent

EXPECTED = {
    # exact batch on the same product
    "INV-001": CONFIRMED,   # MOVICOL, GTIN-13 in stock vs GTIN-14 in the MHRA alert
    "INV-002": CONFIRMED,   # Meropenem "25J-00360" vs "25J00360"; "1 g" vs "1000 mg"
    "INV-003": CONFIRMED,   # Xaggitin XL 27 mg, one of four strengths in the alert
    "INV-004": CONFIRMED,   # falsified Artemether/Lumefantrine seized in Kano
    "INV-005": CONFIRMED,   # Acefyl batch typed in lower case
    "INV-006": CONFIRMED,   # Sporidex voluntary recall
    "INV-007": CONFIRMED,   # Fingolimod; the extracted maker name has a typo ("Zentava")
    "INV-009": CONFIRMED,   # counterfeit "Ulmicort" mimicking Pulmicort
    "INV-025": CONFIRMED,   # Kadin 2 batch "569": short, but name and maker both agree
    # plausible, needs a person
    "INV-008": VERIFY,      # Clear Eyes: alert names the recalling firm, stock lists the distributor
    "INV-010": VERIFY,      # "25JO1305" (letter O) vs "25J01305"
    "INV-011": VERIFY,      # "O9E1" vs "09E1"
    "INV-012": VERIFY,      # ChloraPrep: all lots up to 05/2029, this lot expires 03/2028
    "INV-016": VERIFY,      # Levetiracetam: batch of the 250 mg product on a 500 mg stock line
    # audit trail only
    "INV-019": WITHDRAWN,   # Deleject needle alert uploaded in error and withdrawn by NAFDAC
    # must not match
    "INV-013": None,        # ChloraPrep expiring 08/2029, after the alert's limit
    "INV-014": None,        # Loratadine batch "24" from a different maker than the DRAP recall
    "INV-015": None,        # MOVICOL 474920, next to recalled 474919
    "INV-017": None,        # Paracetamol 3279 shares a lot number with FDA eye drops
    "INV-018": None,        # gabapentin: the NAFDAC notice is a safety communication, not a recall
    "INV-020": None,        # Amoxicillin 24L069 shares a batch number with recalled Nitras
    "INV-021": None,
    "INV-022": None,
    "INV-023": None,
    "INV-024": None,
}


@pytest.fixture(scope="module")
def outcomes():
    items = load_inventory(ROOT / "examples" / "inventory_sample.csv")
    alerts = [json.loads(l) for l in (ROOT / "tests" / "fixtures" / "extracted_alerts.jsonl").read_text(encoding="utf-8").splitlines()]
    best = {}
    for m in match_inventory(items, alerts):
        best.setdefault(m.item["item_id"], m.tier)  # matches arrive strongest first
    return {i["item_id"]: best.get(i["item_id"]) for i in items}


@pytest.mark.parametrize("item_id,tier", EXPECTED.items())
def test_sample_inventory(outcomes, item_id, tier):
    assert outcomes[item_id] == tier


def test_every_sample_line_has_an_expectation(outcomes):
    assert set(outcomes) == set(EXPECTED)


def _alert(**kw):
    base = dict(alert_id="A1", source="TEST", url="", date="", title="", alert_type="recall", alert_status="active",
                product_name="Paracetamol 500 mg Tablets", strength_and_form="", manufacturer="Emzor",
                product_codes=[], batch_number="PX1234", expiry_condition="", reason="")
    base.update(kw)
    return AlertBatch(**base)


def _item(**kw):
    base = dict(item_id="I1", product_name="Paracetamol tablets", strength="500 mg", manufacturer="Emzor",
                batch_number="PX1234", expiry="", gtin="")
    base.update(kw)
    return base


def test_gtin_conflict_blocks_match_even_with_same_batch():
    assert compare(_item(gtin="5012748615328"), _alert(product_codes=["05099999999999"])) is None


def test_short_batch_without_maker_agreement_is_not_confirmed():
    m = compare(_item(batch_number="24", manufacturer=""), _alert(batch_number="24"))
    assert m is not None and m.tier == VERIFY


def test_withdrawn_alert_never_asks_for_action():
    m = compare(_item(), _alert(alert_status="withdrawn"))
    assert m.tier == WITHDRAWN


def test_safety_communication_without_batches_does_not_match():
    assert compare(_item(), _alert(batch_number="", alert_type="safety_communication")) is None


def test_all_batches_respects_expiry_after_condition():
    assert compare(_item(expiry="01/2026"), _alert(batch_number="ALL", expiry_condition="expiring after 06/2026")) is None
    assert compare(_item(expiry="09/2026"), _alert(batch_number="ALL", expiry_condition="expiring after 06/2026")).tier == VERIFY


@pytest.fixture(scope="module")
def fixture_alerts():
    return [json.loads(l) for l in (ROOT / "tests" / "fixtures" / "extracted_alerts.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("batch,gtin,expected", [
    ("487069", "05012748615328", CONFIRMED),   # MOVICOL: batch and GTIN both match the MHRA alert
    ("474920", "05012748615328", None),        # same product, a batch no alert names
    ("487069", "05099999999999", None),        # recalled batch number on a different GTIN: another product
    ("25JO1305", "", VERIFY),                  # look-alike characters
    ("240605", "", WITHDRAWN),                 # alert withdrawn by NAFDAC
    ("24", "", None),                          # a two-character batch alone is not evidence of anything
    ("BP5203", "", VERIFY),                    # batch only: shown with the alert's product for the pharmacist to compare
])
def test_lookup_one_pack(fixture_alerts, batch, gtin, expected):
    from batchwatch.match import lookup_pack
    found = lookup_pack(fixture_alerts, batch, gtin)
    assert (found[0].tier if found else None) == expected


def test_action_follows_alert_type():
    recall = compare(_item(), _alert(alert_type="recall"))
    notice = compare(_item(), _alert(alert_type="other", required_action="Supply with the corrected leaflet"))
    assert recall.tier == notice.tier == CONFIRMED
    assert recall.action.startswith("Quarantine")
    assert "follow the regulator's instruction" in notice.action


def test_normalisers():
    assert norm_batch(" 25j-003/60 ") == "25J00360"
    assert confusable_key("25JO1305") == confusable_key("25J01305")
    assert norm_gtin("05012748615328") == norm_gtin("5012748615328")
    assert (1000.0, "mg") in strengths("Meropenem 1 g")
    assert parse_month("28/02/2029") == (2029, 2) and parse_month("2027-05") == (2027, 5)
    assert overlap({"fingolimod", "zentava"}, {"fingolimod", "zentiva"}) == 1.0
    assert overlap({"kadin"}, {"kadena"}) == 0.0  # short words must match exactly


def test_exact_batch_on_the_alert_brand_is_never_dropped():
    """KRABEVA: the alert adds the generic name, the stock line describes the pack differently."""
    alert = _alert(product_name="KRABEVA 400 mg per 16 ml", strength_and_form="Bevacizumab", manufacturer="Biocon Limited",
                   batch_number="BF25008690")
    m = compare(_item(product_name="KRABEVA oncology infusion ICU", strength="", manufacturer="", batch_number="BF25008690"), alert)
    assert m is not None and m.tier == VERIFY and any("brand name KRABEVA" in r for r in m.reasons)
    # dosage-form words do not dilute the name: the usual description confirms
    m = compare(_item(product_name="KRABEVA 400 mg/16 ml concentrate for infusion", strength="400 mg/16 ml",
                      manufacturer="Biocon", batch_number="BF25008690"), alert)
    assert m.tier == CONFIRMED
    # the same number on another brand is still a coincidence
    assert compare(_item(product_name="Avastin 400 mg", batch_number="BF25008690", manufacturer=""), alert) is None


def test_stock_headers_in_arabic_and_export_styles():
    items, cols = parse_inventory_columns("كود الصنف;اسم الصنف;رقم التشغيلة;Exp. Date;Qty\nRY-1;Lorivan 2mg Tab;104173;05/2027;30\n")
    assert cols["product_name"] == "اسم الصنف" and cols["batch_number"] == "رقم التشغيلة" and cols["expiry"] == "Exp. Date"
    assert items[0]["batch_number"] == "104173" and items[0]["quantity"] == "30"
    assert norm_header("Batch No.") == "batch_no" and norm_header("Lot #") == "lot"


def test_riyadh_example_against_the_registry_seed():
    items = load_inventory(ROOT / "examples" / "riyadh_pharmacy.csv")
    alerts = [json.loads(l) for l in (ROOT / "registry" / "seed_alerts.jsonl").read_text(encoding="utf-8").splitlines()]
    best = {}
    for m in match_inventory(items, alerts):
        best.setdefault(m.item["item_id"], m.tier)
    assert best == {"RY-001": CONFIRMED, "RY-002": CONFIRMED, "RY-004": CONFIRMED, "RY-005": CONFIRMED,
                    "RY-006": CONFIRMED, "RY-007": CONFIRMED, "RY-008": VERIFY, "RY-009": CONFIRMED, "RY-010": CONFIRMED}
