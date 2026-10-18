"""Score batch extraction against the hand-labelled gold set.

Usage:
  python eval/evaluate_extraction.py --model nano            # call the model on every snapshot alert
  python eval/evaluate_extraction.py --model nano --reuse    # re-score saved predictions without calling the model

Writes eval/predictions_<model>.jsonl and eval/results_<model>.json.
"""
import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from batchwatch.extract import extract_alert, run_checks  # noqa: E402
from batchwatch.llm import MODELS, UsageMeter, make_client, out_of_credit  # noqa: E402

EVAL = ROOT / "eval"


def norm_batch(b: str) -> str:
    return re.sub(r"[\s\-/.]", "", str(b)).upper()


def norm_name(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def product_has(name: str, keyword: str) -> bool:
    return re.search(r"(?<![0-9])" + re.escape(norm_name(keyword)), norm_name(name)) is not None


def load_gold(alerts: list[dict]) -> dict:
    gold = json.loads((EVAL / "gold_batches.json").read_text(encoding="utf-8"))["alerts"]
    by_id = {}
    for g in gold:
        hits = [a["alert_id"] for a in alerts if g["alert"] in a["alert_id"]]
        if len(hits) != 1:
            raise SystemExit(f"gold entry {g['alert']!r} matches {len(hits)} alerts, expected 1")
        by_id[hits[0]] = g
    missing = [a["alert_id"] for a in alerts if a["alert_id"] not in by_id]
    if missing:
        raise SystemExit(f"alerts without gold labels: {missing}")
    return by_id


def score_alert(pred: dict, gold: dict) -> dict:
    """Compare one prediction with its gold entry and list every disagreement."""
    record = pred.get("record") or {}
    issues = []
    if pred.get("error"):
        issues.append(f"model error: {pred['error'][:80]}")

    predicted = {}  # normalised batch -> product identity (name plus strength and form)
    has_all = False
    for p in record.get("products", []) or []:
        identity = f"{p.get('product_name') or ''} {p.get('strength_and_form') or ''}"
        for b in p.get("batches", []) or []:
            n = str(b.get("batch_number", "")).strip()
            if n.upper() == "ALL":
                has_all = True
            elif n:
                predicted.setdefault(norm_batch(n), identity)

    gold_batches = {norm_batch(b): kw for b, kw in gold["batches"]}
    tp = set(predicted) & set(gold_batches)
    fp = set(predicted) - set(gold_batches)
    fn = set(gold_batches) - set(predicted)
    if fp:
        issues.append(f"extra batches: {sorted(fp)}")
    if fn:
        issues.append(f"missed batches: {sorted(fn)}")

    wrong_product = [b for b in tp if gold_batches[b] and not product_has(predicted[b], gold_batches[b])]
    if wrong_product:
        issues.append(f"batch attached to wrong product: {sorted(wrong_product)}")

    if gold.get("all") and not has_all:
        issues.append("expected ALL batches, model did not say so")
    if not gold.get("all") and not gold.get("none_or_all") and has_all:
        issues.append("model said ALL batches, but the alert does not cover all batches")

    status = (record.get("alert_status") or "active").lower()
    if status != gold.get("status", "active"):
        issues.append(f"status {status!r}, expected {gold.get('status', 'active')!r}")

    return {"tp": len(tp), "fp": len(fp), "fn": len(fn), "product_checked": sum(1 for b in tp if gold_batches[b]),
            "product_wrong": len(wrong_product), "issues": issues}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="nano", choices=MODELS)
    ap.add_argument("--fallback", default="super", choices=[*MODELS, "none"],
                    help="stronger model used when a reply fails validation")
    ap.add_argument("--reuse", action="store_true", help="score saved predictions instead of calling the model")
    ap.add_argument("--recheck", action="store_true",
                    help="with --reuse: re-apply today's deterministic cleanup and checks to the saved replies first")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    alerts = [json.loads(l) for l in (EVAL / "alerts_snapshot.jsonl").read_text(encoding="utf-8").splitlines() if l]
    gold = load_gold(alerts)
    run_name = args.model if args.fallback == "none" else f"{args.model}+{args.fallback}"
    pred_path = EVAL / f"predictions_{run_name}.jsonl"
    model_id = MODELS[args.model][0]
    fallback_id = None if args.fallback == "none" else MODELS[args.fallback][0]
    meter = UsageMeter()

    if args.reuse:
        preds = {p["alert_id"]: p for p in map(json.loads, pred_path.read_text(encoding="utf-8").splitlines())}
        if args.recheck:  # no model call: the stored replies go through the current cleanup and checks
            by_id = {a["alert_id"]: a for a in alerts}
            for aid, p in preds.items():
                if p.get("record"):
                    p.update(run_checks(p["record"], by_id[aid]))
    else:
        load_dotenv(ROOT / ".env")
        client = make_client()
        def timed(a):
            start = time.perf_counter()
            out = extract_alert(client, model_id, a, meter, fallback_id)
            return {**out, "latency_s": round(time.perf_counter() - start, 2)}

        with ThreadPoolExecutor(args.workers) as pool:
            outs = list(pool.map(timed, alerts))
        if any(out_of_credit(o.get("error")) for o in outs):
            raise SystemExit("Token Factory balance exhausted (HTTP 402): nothing written, earlier results kept.")
        preds = {a["alert_id"]: {"alert_id": a["alert_id"], "model": model_id, **o} for a, o in zip(alerts, outs)}
        with open(pred_path, "w", encoding="utf-8") as f:
            for a in alerts:
                f.write(json.dumps(preds[a["alert_id"]], ensure_ascii=False) + "\n")

    totals = {"tp": 0, "fp": 0, "fn": 0, "product_checked": 0, "product_wrong": 0}
    perfect = 0
    per_alert = {}
    for a in alerts:
        s = score_alert(preds[a["alert_id"]], gold[a["alert_id"]])
        per_alert[a["alert_id"]] = s
        for k in totals:
            totals[k] += s[k]
        if not s["issues"]:
            perfect += 1
        else:
            print(f"[{a['source']}] {a['title'][:70]}")
            for i in s["issues"]:
                print(f"    - {i}")

    results_path = EVAL / f"results_{run_name}.json"
    earlier = json.loads(results_path.read_text(encoding="utf-8"))["summary"] if results_path.exists() else {}
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    summary = {
        "model": model_id,
        "alerts": len(alerts),
        "alerts_fully_correct": perfect,
        "batches_gold": tp + fn,
        "batches_correct": tp,
        "batches_extra": fp,
        "batches_missed": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "product_attachment_correct": f"{totals['product_checked'] - totals['product_wrong']}/{totals['product_checked']}",
        "escalated_to_fallback": sum(1 for p in preds.values() if p.get("escalated_because")),
        "cost_usd": round(meter.cost_usd, 4) if not args.reuse else earlier.get("cost_usd"),  # a re-score costs nothing;
        "calls_by_model": meter.calls_by_model if not args.reuse else earlier.get("calls_by_model"),  # keep the run's own
        "median_latency_s": (round(statistics.median(lat), 2)
                             if (lat := [p["latency_s"] for p in preds.values() if p.get("latency_s")]) else None),
    }
    (EVAL / f"results_{run_name}.json").write_text(
        json.dumps({"summary": summary, "per_alert": per_alert}, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
