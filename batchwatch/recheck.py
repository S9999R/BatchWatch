"""Re-apply today's cleanup and checks to every stored reading, without calling a model.

When a check is added or tightened, readings stored earlier still carry the old result. This brings them up to
date: fragments of broken replies are dropped, batch numbers are cleaned, and every check is run again on the
stored notice text. Readings that now fail a check are listed, so they can be read again when credit allows
(python -m batchwatch.pipeline re-reads nothing; the watcher only reads new alerts).

Usage: python -m batchwatch.recheck            update <data dir>/alerts.jsonl in place
       python -m batchwatch.recheck --seed     also rebuild registry/seed_alerts.jsonl's checks
"""
import argparse
import copy
import json

from batchwatch.extract import needs_escalation, run_checks
from batchwatch.pipeline import ALERT_FIELDS, SEED, live_registry, read_registry, write_registry

CHECKED = ("grounding", "missed_candidates", "name_words_not_in_source", "all_batches_missing", "misattached_batches",
           "broken_fragments", "error")


def recheck_rows(rows: list[dict]) -> list[str]:
    """Update rows in place; return the ids whose reading still fails a check."""
    failing = []
    for e in rows:
        if not e.get("record") or not e.get("text"):
            continue
        alert = {k: e.get(k, "") for k in ALERT_FIELDS} | {"text": e["text"]}
        result = run_checks(copy.deepcopy(e["record"]), alert)
        e["record"] = result["record"]
        e["checks"] = {**(e.get("checks") or {}), **{k: result[k] for k in CHECKED}}
        if needs_escalation(result):
            failing.append(e["alert_id"])
    return failing


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", action="store_true")
    args = ap.parse_args()
    if live_registry().exists():
        rows = read_registry()
        failing = recheck_rows(rows)
        write_registry(rows)
        print(f"{live_registry().name}: {len(rows)} readings re-checked; still failing a check: {failing}")
    if args.seed:
        rows = [json.loads(l) for l in SEED.read_text(encoding="utf-8").splitlines() if l.strip()]
        failing = recheck_rows(rows)
        SEED.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(f"{SEED.name}: {len(rows)} readings re-checked; still failing a check: {failing}")


if __name__ == "__main__":
    main()
