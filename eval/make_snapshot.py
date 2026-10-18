"""Freeze a set of real regulator alerts so extraction can be scored reproducibly.

Usage:
  python eval/make_snapshot.py --per-source 15                        # rebuild from every source
  python eval/make_snapshot.py --per-source 15 --sources sfda --append  # add one source, keep the rest
Writes eval/alerts_snapshot.jsonl (one alert per line). Gold labels live in eval/gold_batches.json.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from batchwatch.sources import FETCHERS  # noqa: E402

OUT = ROOT / "eval" / "alerts_snapshot.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-source", type=int, default=15)
    ap.add_argument("--sources", default=",".join(FETCHERS))
    ap.add_argument("--append", action="store_true", help="keep existing alerts and add only new ids")
    args = ap.parse_args()

    existing = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l] if args.append and OUT.exists() else []
    known = {a["alert_id"] for a in existing}
    added = []
    for name in args.sources.split(","):
        got = [a for a in FETCHERS[name.strip()](args.per_source) if a["alert_id"] not in known]
        print(f"{name}: {len(got)} new alerts")
        added.extend(got)

    with open(OUT, "w", encoding="utf-8") as f:
        for a in existing + [{**a, "snapshot_date": date.today().isoformat()} for a in added]:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    print(f"Wrote {len(existing) + len(added)} alerts to {OUT}")


if __name__ == "__main__":
    main()
