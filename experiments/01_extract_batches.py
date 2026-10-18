"""Experiment 1: can Nemotron pull batch numbers out of real regulator alerts reliably?

Fetches recent alerts from FDA (US), MHRA (UK) and NAFDAC (Nigeria), extracts batch-level records
with a Nemotron model on Nebius Token Factory, and writes:
  data/alerts_extracted.jsonl  every alert with its extracted record and grounding checks
  data/labeling_sheet.csv      one row per alert for hand-labelling the correct batch numbers

Usage (from the project root):
  python experiments/01_extract_batches.py --dry-run            # fetch only, no model calls
  python experiments/01_extract_batches.py --per-source 5       # fetch + extract with Nemotron 3 Nano
  python experiments/01_extract_batches.py --model super
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from batchwatch.extract import extract_alert  # noqa: E402
from batchwatch.llm import MODELS, UsageMeter, make_client  # noqa: E402
from batchwatch.sources import FETCHERS  # noqa: E402

DATA = ROOT / "data"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-source", type=int, default=5, help="alerts to fetch from each regulator")
    ap.add_argument("--sources", default=",".join(FETCHERS), help="comma list of: " + ", ".join(FETCHERS))
    ap.add_argument("--model", default="nano", choices=MODELS)
    ap.add_argument("--fallback", default="super", choices=[*MODELS, "none"],
                    help="stronger model used when a reply fails validation")
    ap.add_argument("--dry-run", action="store_true", help="fetch alerts only, skip model calls")
    ap.add_argument("--retry-failed", action="store_true",
                    help="re-run only the alerts that errored in the last run's alerts_extracted.jsonl")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    DATA.mkdir(exist_ok=True)

    fallback_id = None if args.fallback == "none" else MODELS[args.fallback][0]
    if args.retry_failed:
        retry_failed(args.model, fallback_id)
        return

    alerts = []
    for name in args.sources.split(","):
        try:
            got = FETCHERS[name.strip()](args.per_source)
            print(f"[fetch] {name}: {len(got)} alerts")
            alerts.extend(got)
        except Exception as e:
            print(f"[fetch] {name}: FAILED ({type(e).__name__}: {e})")

    if args.dry_run:
        for a in alerts:
            print(f"\n=== {a['source']} | {a['date']} | {a['title'][:90]}\n{a['text'][:400]}")
        print(f"\n{len(alerts)} alerts fetched. Run without --dry-run to extract with Nemotron.")
        return

    model_id = MODELS[args.model][0]
    client = make_client()
    meter = UsageMeter()

    results = []
    for i, a in enumerate(alerts, 1):
        results.append(run_one(client, model_id, fallback_id, a, meter, f"{i}/{len(alerts)}"))
    write_outputs(results, model_id, meter)


ALERT_KEYS = ("source", "alert_id", "url", "date", "title", "text")


def run_one(client, model_id: str, fallback_id: str | None, alert: dict, meter: UsageMeter, label: str) -> dict:
    out = extract_alert(client, model_id, alert, meter, fallback_id)
    if out["error"]:
        status = "ERROR " + out["error"][:80]
    else:
        n = len(out["grounding"])
        ungrounded = sum(not g["in_source"] for g in out["grounding"])
        status = f"{n} batches" + (f", {ungrounded} NOT IN SOURCE" if ungrounded else "")
    if out.get("escalated_because"):
        status += f"  (escalated to {out['model_used']})"
    print(f"[{label}] {alert['source']:<16} {alert['title'][:60]:<60} -> {status}")
    return {**alert, "model": out["model_used"], **out}


def retry_failed(model_key: str, fallback_id: str | None) -> None:
    path = DATA / "alerts_extracted.jsonl"
    results = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    failed = [i for i, r in enumerate(results) if r.get("error")]
    print(f"Retrying {len(failed)} of {len(results)} alerts")
    model_id = MODELS[model_key][0]
    client = make_client()
    meter = UsageMeter()
    for n, i in enumerate(failed, 1):
        alert = {k: results[i][k] for k in ALERT_KEYS}
        results[i] = run_one(client, model_id, fallback_id, alert, meter, f"{n}/{len(failed)}")
    write_outputs(results, model_id, meter)


def write_outputs(results: list[dict], model_id: str, meter: UsageMeter) -> None:
    with open(DATA / "alerts_extracted.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # utf-8-sig so Excel opens it with the right encoding
    with open(DATA / "labeling_sheet.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "alert_id", "date", "title", "url", "model_batches", "all_in_source",
                    "correct_batches (fill by hand)", "notes"])
        for r in results:
            batches = "; ".join(g["batch_number"] for g in r["grounding"])
            grounded = all(g["in_source"] for g in r["grounding"]) if r["grounding"] else ""
            w.writerow([r["source"], r["alert_id"], r["date"], r["title"], r["url"], batches, grounded, "",
                        r["error"] or ""])

    total_batches = sum(len(r["grounding"]) for r in results)
    ungrounded = sum(not g["in_source"] for r in results for g in r["grounding"])
    errors = sum(1 for r in results if r["error"])
    print(f"\nModel: {model_id}")
    print(f"Alerts: {len(results)}  errors: {errors}  batches extracted: {total_batches}  not found in source: {ungrounded}")
    print(f"Tokens: {meter.prompt_tokens} in / {meter.completion_tokens} out  ->  ${meter.cost_usd:.4f}")
    print(f"Wrote {DATA / 'alerts_extracted.jsonl'} and {DATA / 'labeling_sheet.csv'}")


if __name__ == "__main__":
    main()
