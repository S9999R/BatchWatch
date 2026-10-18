"""Experiment 2: match a stock list against extracted regulator alerts and write an action report.

Usage (from the project root):
  python experiments/02_match_inventory.py                                    # sample stock vs frozen alerts
  python experiments/02_match_inventory.py --alerts data/alerts_extracted.jsonl --inventory my_stock.csv

Writes data/matches.csv. A stock line that does not appear in the report is not declared safe: it only means
none of the covered alerts names it.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from batchwatch.match import load_inventory, match_inventory  # noqa: E402

DATA = ROOT / "data"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alerts", default=str(ROOT / "tests" / "fixtures" / "extracted_alerts.jsonl"))
    ap.add_argument("--inventory", default=str(ROOT / "examples" / "inventory_sample.csv"))
    ap.add_argument("--out", default=str(DATA / "matches.csv"))
    args = ap.parse_args()

    alerts = [json.loads(l) for l in Path(args.alerts).read_text(encoding="utf-8").splitlines() if l.strip()]
    items = load_inventory(args.inventory)
    matches = match_inventory(items, alerts)

    sources = Counter(a.get("source", "?") for a in alerts)
    print(f"Checked {len(items)} stock lines against {len(alerts)} alerts from "
          + ", ".join(f"{s} ({n})" for s, n in sources.items()))
    tiers = Counter(m.tier for m in matches)
    print("Results: " + ", ".join(f"{t} {n}" for t, n in tiers.items()) if matches else "Results: no matches")

    for m in matches:
        i, a = m.item, m.alert
        print(f"\n[{m.tier}] {i['product_name']} | batch {i['batch_number']} | qty {i.get('quantity', '?')} "
              f"| {i.get('location', '')}")
        print(f"    alert: {a.source} {a.date} | {a.title[:90]}")
        print(f"    why:   {'; '.join(m.reasons)}")
        print(f"    do:    {m.action}")
        print(f"    see:   {a.url}")

    Path(args.out).parent.mkdir(exist_ok=True)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tier", "action", "item_id", "product_name", "batch_number", "quantity", "location",
                    "alert_source", "alert_date", "alert_type", "alert_product", "alert_batch", "alert_reason",
                    "why", "alert_url"])
        for m in matches:
            i, a = m.item, m.alert
            w.writerow([m.tier, m.action, i["item_id"], i["product_name"], i["batch_number"], i.get("quantity", ""),
                        i.get("location", ""), a.source, a.date, a.alert_type, a.product_name, a.batch_number,
                        a.reason, "; ".join(m.reasons), a.url])
    print(f"\nWrote {args.out}")
    print("Lines not listed were not named by any covered alert. That is not a guarantee they are safe.")


if __name__ == "__main__":
    main()
