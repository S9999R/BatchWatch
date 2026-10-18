"""Extraction safety-net tests: the deterministic checks around the model, run offline."""
import json
from pathlib import Path

from batchwatch.extract import (appears_in, candidate_batches, check_grounding, clean_batch_numbers,
                                missed_candidates, needs_escalation, normalise_record, parse_json_reply,
                                ungrounded_name_words)
from batchwatch.sources import html_to_text

ROOT = Path(__file__).resolve().parent.parent


def test_clean_batch_numbers():
    assert clean_batch_numbers("Lot# 3161") == ["3161"]
    assert clean_batch_numbers("Lot #: RL25G04") == ["RL25G04"]
    assert clean_batch_numbers("Batch No. 487069") == ["487069"]
    assert clean_batch_numbers("BN 4L01372H") == ["4L01372H"]
    assert clean_batch_numbers("3298, 3299 and 3300") == ["3298", "3299", "3300"]
    assert clean_batch_numbers("LOT123") == ["LOT123"]  # "LOT" glued to digits is part of the identifier


def test_appears_in_needs_whole_identifier():
    text = "Batch No. | 24L069 | Tablet Loratadine 24 | 25J 00360"
    assert appears_in("24L069", text)
    assert appears_in("25J00360", text)  # tolerates a space inside
    assert not appears_in("4L06", text)  # fragments do not count
    assert not appears_in("069", text)


def test_hallucinated_batch_is_flagged():
    record = {"products": [{"product_name": "X", "batches": [{"batch_number": "999999"}]}]}
    assert check_grounding(record, "Batch 487069")[0]["in_source"] is False


def test_reply_parsing_tolerates_reasoning_and_loose_shapes():
    record = parse_json_reply('<think>...</think>```json\n{"products": ["Kadin 2"], "alert_status": "active"}\n```')
    record = normalise_record(record)
    assert record["products"] == [{"product_name": "Kadin 2", "batches": []}]
    loose = normalise_record({"products": [{"product_name": "Y", "batches": ["Lot# 3161", "3162"]}]})
    assert [b["batch_number"] for b in loose["products"][0]["batches"]] == ["3161", "3162"]


def test_candidate_reader_never_invents_on_the_gold_set():
    """The deterministic reader only triggers escalation, so it must not report non-batches."""
    import re
    gold = json.loads((ROOT / "eval" / "gold_batches.json").read_text(encoding="utf-8"))["alerts"]
    alerts = [json.loads(l) for l in (ROOT / "eval" / "alerts_snapshot.jsonl").read_text(encoding="utf-8").splitlines()]
    norm = lambda b: re.sub(r"[\s\-/.]", "", b).upper()  # noqa: E731
    for g in gold:
        alert = next(a for a in alerts if g["alert"] in a["alert_id"])
        allowed = {norm(b) for b, _ in g["batches"]}
        assert {norm(c) for c in candidate_batches(alert["text"])} <= allowed, alert["alert_id"]


def test_missed_candidates_trigger_escalation():
    text = "Batch No. | Expiry Date |\n72500107 | 31/10/2029 |"
    record = {"products": [{"product_name": "Benzylpenicillin", "batches": []}]}
    result = {"error": None, "grounding": [], "missed_candidates": missed_candidates(record, text),
              "name_words_not_in_source": []}
    assert result["missed_candidates"] == ["72500107"]
    assert "missed" in needs_escalation(result)


def test_all_batches_phrase_without_all_entry_escalates():
    from batchwatch.extract import all_batches_missing
    text = "Product | Batch No. |\nBel Ice Vodka Energy Drink | All Batches |"
    assert all_batches_missing({"products": [{"product_name": "Bel Ice", "batches": []}]}, text)
    assert not all_batches_missing({"products": [{"product_name": "Bel Ice", "batches": [{"batch_number": "ALL"}]}]}, text)
    assert not all_batches_missing({"products": []}, "Batch 487069 is recalled")


def test_all_batches_rule_needs_recall_context():
    from batchwatch.extract import says_all_batches_affected
    assert says_all_batches_affected("Batch No.: All batches\nRecall Level: Retail")
    assert says_all_batches_affected("The recall applies to all batches with an expiry date up to 05/2029.")
    assert not says_all_batches_affected(
        "All batches of imported Glycerin and Propylene Glycol raw materials should be accompanied by a certificate.")


def test_unsupported_all_is_removed_but_product_kept():
    from batchwatch.extract import drop_unsupported_all
    glycerin = "All batches of imported Glycerin raw materials should be accompanied by a certificate of analysis."
    record = {"products": [{"product_name": "Glycerin", "batches": [{"batch_number": "ALL"}]}]}
    assert drop_unsupported_all(record, glycerin) is True
    assert record["products"] == [{"product_name": "Glycerin", "batches": []}]
    recall = "Batch No.: All batches\nRecall Level: Retail"
    kept = {"products": [{"product_name": "Motilium", "batches": [{"batch_number": "ALL"}]}]}
    assert drop_unsupported_all(kept, recall) is False and kept["products"][0]["batches"]


def test_reader_fills_missed_batches_only_for_single_product_alerts():
    from batchwatch.extract import fill_from_reader
    single = {"error": None, "missed_candidates": ["112546"], "grounding": [],
              "record": {"products": [{"product_name": "Credanil", "batches": [{"batch_number": "117876"}]}]}}
    fill_from_reader(single, "112546 117876")
    assert [b["batch_number"] for b in single["record"]["products"][0]["batches"]] == ["117876", "112546"]
    assert single["batches_added_by_reader"] == ["112546"] and single["missed_candidates"] == []
    multi = {"error": None, "missed_candidates": ["569"], "grounding": [],
             "record": {"products": [{"product_name": "A", "batches": []}, {"product_name": "B", "batches": []}]}}
    fill_from_reader(multi, "569")
    assert multi["record"]["products"][0]["batches"] == [] and multi["missed_candidates"] == ["569"]


def test_lots_stacked_in_one_cell_are_split():
    from batchwatch.extract import split_row_batches
    text = ("Brand\n| Product Name\n| Lot Number\n|\nSanis\n| CARVEDILOL\n| 665960\n663437\n659717\n|")
    assert split_row_batches(text) == {"665960", "663437", "659717"}


def test_split_line_tables_are_read_row_by_row():
    from batchwatch.extract import split_row_batches
    text = ("Product Name\n| Active Ingredient\n| Batch No.\n| Manufacturer\n|\nTablet Nitras\n| Glyceryl Trinitrate\n"
            "| 24L069\n| M/s Caraway Pharma\n|\nTablet Kadin 2\n| Tizanidine\n| 569\n| M/s FYNK Pharma\n|")
    assert split_row_batches(text) == {"24L069", "569"}


def test_flattened_sfda_row_skips_registration_numbers():
    from batchwatch.extract import flattened_row_batches
    row = ("Trade Name Scientific Name Register Number Affected batch number Manufacturer "
           "KRABEVA 400 mg per 16 ml Bevacizumab 0905245271 BF25008690 Biocon Limited")
    assert flattened_row_batches(row) == {"BF25008690"}


def test_batch_filed_under_wrong_strength_is_caught():
    from batchwatch.extract import misattached_batches
    text = ("Clobazam 5 mg/5 mL Oral Suspension\nBatch No. | Expiry |\n0162858 | 05/2028 |\n0163605 | 07/2028 |\n"
            "Clobazam 10 mg/5 mL Oral Suspension\nBatch No. | Expiry |\n0163858 | 07/2028 |")
    record = {"products": [
        {"product_name": "Clobazam 5 mg/5 mL Oral Suspension", "batches": [{"batch_number": "0162858"}]},
        {"product_name": "Clobazam 10 mg/5 mL Oral Suspension",
         "batches": [{"batch_number": "0163605"}, {"batch_number": "0163858"}]},
    ]}
    assert misattached_batches(record, text) == ["0163605"]


def test_misspelled_product_name_is_caught():
    record = {"products": [{"product_name": "Fingolimod Zentava 0.5 mg Capsules"}]}
    assert ungrounded_name_words(record, "Fingolimod Zentiva 0.5 mg Capsules recall") == ["Zentava"]


def test_html_tables_keep_rows_on_one_line():
    html = "<table><tr><td>Batch No.</td>\n<td>Expiry</td></tr><tr><td>487069</td><td>28/02/2029</td></tr></table>"
    lines = html_to_text(html).split("\n")
    assert "Batch No. | Expiry |" in lines[0] and lines[1].startswith("487069 |")


def test_batch_numbers_are_latin_and_closed_up():
    assert clean_batch_numbers("2604- 54TL3") == ["2604-54TL3"]  # DRAP table cell with a space after the hyphen
    assert clean_batch_numbers("مرفقبالتعميم") == []  # "attached to the circular": Nano's one invented batch
    assert appears_in("2604-54TL3", "Lasani Healthcare | 2604- 54TL3 | Substandard")
    assert not appears_in("24", "Expiry 2024")


def test_broken_reply_fragments_are_not_products():
    from batchwatch.extract import run_checks
    alert = {"source": "DRAP (Pakistan)", "title": "Recall Alert", "text": "Zindagi syringe batch 3-01B-26 is substandard."}
    record = {"alert_type": "recall", "products": [
        {"product_name": "Zindagi", "batches": [{"batch_number": "3-01B-26"}]},
        {"product_name": 'alert_status": ..., malformed. Let\'s fix.', "batches": []},
        {"product_name": "reason", "batches": []}, {"product_name": "countries", "batches": []}]}
    result = run_checks(record, alert)
    assert [p["product_name"] for p in result["record"]["products"]] == ["Zindagi"]
    assert result["broken_fragments"] == 3 and "broken JSON fragments" in needs_escalation(result)
