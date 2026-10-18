"""Registry storage and a full refresh: fetch every source, extract new alerts with Nemotron, append them.

Usage (from the project root):
  python -m batchwatch.pipeline --per-source 15

The scheduled path is batchwatch.watch, which asks each source what is new before fetching anything.
Alerts already in the registry (same alert_id) are skipped, so a run only pays for new alerts.
The live registry is <data dir>/alerts.jsonl (BATCHWATCH_DATA_DIR, default registry/), seeded from
registry/seed_alerts.jsonl, the 90 alerts of the evaluation.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from batchwatch.extract import extract_alert
from batchwatch.llm import MODELS, UsageMeter, make_client
from batchwatch.sources import FETCHERS

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "registry" / "seed_alerts.jsonl"
TEXT_CHARS = 12_000  # the notice text is kept so the site can show where each batch number came from


def data_dir() -> Path:
    return Path(os.environ.get("BATCHWATCH_DATA_DIR") or ROOT / "registry")


def live_registry() -> Path:
    return data_dir() / "alerts.jsonl"

ALERT_FIELDS = ("source", "alert_id", "url", "date", "title")
CHECK_FIELDS = ("grounding", "missed_candidates", "name_words_not_in_source", "all_batches_missing",
                "misattached_batches", "broken_fragments", "error", "model_used", "escalated_because")


def registry_path() -> Path:
    return live_registry() if live_registry().exists() else SEED


def read_registry() -> list[dict]:
    path = registry_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_registry(rows: list[dict]) -> None:
    """Write the whole registry atomically, so the web app never reads a half-written file."""
    path = live_registry()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def to_entry(alert: dict, result: dict, detected_at: str | None = None, backfill: bool = False) -> dict:
    """Registry row: the alert's identity and text, the extracted record and the outcome of every check.
    detected_at is when BatchWatch first saw the alert; backfill marks alerts found on a source's first run,
    which were published before BatchWatch was watching and so say nothing about detection speed."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {**{k: alert.get(k, "") for k in ALERT_FIELDS},
            "published_at": alert.get("published_at") or alert.get("date", ""),
            "record": result.get("record"),
            "checks": {k: result.get(k) for k in CHECK_FIELDS},
            "text": (alert.get("text") or "")[:TEXT_CHARS],
            "extracted_at": now,
            "detected_at": detected_at or now,
            "backfill": backfill}


def update(per_source: int, model_key: str = "nano", fallback_key: str = "super") -> dict:
    existing = read_registry()
    known = {e["alert_id"] for e in existing}
    client = make_client()
    meter = UsageMeter()
    model_id, fallback_id = MODELS[model_key][0], MODELS[fallback_key][0]

    added, failed = [], []
    for name, fetch in FETCHERS.items():
        try:
            alerts = fetch(per_source)
        except Exception as e:
            print(f"[{name}] fetch failed: {type(e).__name__}: {e}")
            continue
        new = [a for a in alerts if a["alert_id"] not in known]
        print(f"[{name}] {len(alerts)} fetched, {len(new)} new")
        for a in new:
            result = extract_alert(client, model_id, a, meter, fallback_id)
            if result.get("error"):
                failed.append(a["alert_id"])  # retried on the next run, never stored half-done
                continue
            added.append(to_entry(a, result))
            known.add(a["alert_id"])

    rows = existing + added
    write_registry(rows)
    summary = {"added": len(added), "failed": failed, "total": len(rows), "cost_usd": round(meter.cost_usd, 4),
               "calls_by_model": meter.calls_by_model}
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-source", type=int, default=15)
    ap.add_argument("--model", default="nano", choices=MODELS)
    ap.add_argument("--fallback", default="super", choices=MODELS)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    update(args.per_source, args.model, args.fallback)


if __name__ == "__main__":
    main()
