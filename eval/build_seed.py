"""Rebuild registry/seed_alerts.jsonl (what the web app serves without a live registry) from a scored run.

Usage: python eval/build_seed.py eval/predictions_nano+super_run_U3a.jsonl
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from batchwatch.extract import drop_unsupported_all, type_from_title  # noqa: E402
from batchwatch.pipeline import SEED, to_entry  # noqa: E402


def main() -> None:
    preds = [json.loads(l) for l in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if l]
    snap = {a["alert_id"]: a for a in map(json.loads, (ROOT / "eval" / "alerts_snapshot.jsonl").read_text(encoding="utf-8").splitlines())}
    rows = []
    for p in preds:
        alert = snap[p["alert_id"]]
        if p.get("record"):  # apply the deterministic rules added after this run was recorded
            type_from_title(alert, p["record"])
            p["record"]["all_removed_unsupported"] = drop_unsupported_all(p["record"], alert["text"])
        seen = alert.get("snapshot_date", "") + "T00:00:00+00:00"
        entry = to_entry(alert, p, detected_at=seen, backfill=True)  # read in a batch, not caught by the watcher
        entry["extracted_at"] = seen
        rows.append(entry)
    SEED.parent.mkdir(exist_ok=True)
    with open(SEED, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"seed: {len(rows)} alerts from {len({r['source'] for r in rows})} sources")


if __name__ == "__main__":
    main()
