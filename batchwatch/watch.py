"""Smart update: watch the regulators and read only what is new.

No regulator pushes its alerts, so BatchWatch polls them, cheaply. Each run asks every source one light
question first (an API listing or a list page, sent with ETag / Last-Modified when the server gave them):
which alerts are newest? Only alerts the registry does not have yet are downloaded in full, and only those
are read by Nemotron. A quiet run downloads a few small pages and makes no model call at all.

Every run is logged to <data dir>/runs.jsonl and every source's state to <data dir>/watch_state.json, so the
site can show when each regulator was last checked and how fast a new alert was picked up.

Usage:
  python -m batchwatch.watch               one pass over every source (what the scheduler runs)
  python -m batchwatch.watch --dry-run     list what is new without downloading notices or calling a model
  python -m batchwatch.watch --only sfda   one source

Limits (environment): WATCH_MAX_NEW_PER_RUN (default 25), WATCH_DAILY_USD (default 0.50). Alerts over the
limit are left for the next run, never dropped.
"""
import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx
from dotenv import load_dotenv

from batchwatch import sources as S
from batchwatch.pipeline import ROOT, data_dir, read_registry, to_entry, write_registry

PER_SOURCE = 15
RUNS_KEPT = 2000


@dataclass
class Source:
    name: str  # display name, as stored in each alert's "source"
    list_new: Callable  # (client, limit, state) -> list of items, newest first; each has "alert_id"
    detail: Callable | None = None  # (client, item) -> full alert; None when the listing already has the text


def _conditional_get(c: httpx.Client, url: str, state: dict, **kw) -> httpx.Response | None:
    """GET with the validators the server sent last time. None means "not modified" (HTTP 304).
    New validators are only staged: the run keeps them once every new alert of the source has been read, so an
    alert left for the next run (budget, error) is not hidden behind a 304."""
    headers = {}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    r = c.get(url, headers=headers, **kw)
    if r.status_code == 304:
        return None
    r.raise_for_status()
    staged = state.setdefault("_staged", {})
    for header, key in (("etag", "etag"), ("last-modified", "last_modified")):
        if r.headers.get(header):
            staged[key] = r.headers[header]
    return r


def _whole(fetch: Callable) -> Callable:
    """Sources whose listing is a single request that already carries the notice text (APIs, one table)."""
    return lambda c, limit, state: fetch(limit)


def _list_health_canada(c: httpx.Client, limit: int, state: dict) -> list[dict]:
    r = _conditional_get(c, S.HC_JSON, state, timeout=120)  # a 15 MB index: skipped entirely when unchanged
    return [] if r is None else S.health_canada_items(r.json(), limit)


def _list_who(c: httpx.Client, limit: int, state: dict) -> list[dict]:
    r = _conditional_get(c, S.WHO_LIST, state)
    return [] if r is None else S.who_items(r.text, limit)


SOURCES = {
    "sfda": Source("SFDA (Saudi Arabia)", lambda c, limit, state: S.list_sfda(c, limit), S.detail_sfda),
    "fda": Source("FDA (US)", _whole(S.fetch_fda)),
    "mhra": Source("MHRA (UK)", lambda c, limit, state: S.list_mhra(c, limit), S.detail_mhra),
    "health_canada": Source("Health Canada", _list_health_canada, S.detail_health_canada),
    "nafdac": Source("NAFDAC (Nigeria)", _whole(S.fetch_nafdac)),
    "drap": Source("DRAP (Pakistan)", _whole(S.fetch_drap)),
    "sahpra": Source("SAHPRA (South Africa)", _whole(S.fetch_sahpra)),
    "rwanda": Source("Rwanda FDA", _whole(S.fetch_rwanda)),
    "fda_ghana": Source("FDA Ghana", _whole(S.fetch_fda_ghana)),
    "who": Source("WHO", _list_who, S.detail_who),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_path():
    return data_dir() / "watch_state.json"


def runs_path():
    return data_dir() / "runs.jsonl"


def load_state() -> dict:
    p = state_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def read_runs(limit: int | None = None) -> list[dict]:
    p = runs_path()
    if not p.exists():
        return []
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [json.loads(l) for l in (lines[-limit:] if limit else lines)]


def append_run(run: dict) -> None:
    runs = read_runs()[-(RUNS_KEPT - 1):] + [run]
    p = runs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")


def _summary(entry: dict) -> dict:
    rec = entry.get("record") or {}
    return {"alert_id": entry["alert_id"], "source": entry["source"], "title": entry.get("title", ""),
            "type": rec.get("alert_type"), "url": entry.get("url"), "published_at": entry.get("published_at"),
            "detected_at": entry.get("detected_at"), "backfill": entry.get("backfill", False),
            "batches": sum(len(p.get("batches", [])) for p in rec.get("products", []) or []),
            "model": (entry.get("checks") or {}).get("model_used")}


def run_once(only: list[str] | None = None, dry_run: bool = False, per_source: int = PER_SOURCE,
             extract: Callable | None = None, notify: Callable | None = None) -> dict:
    """One pass over the sources. extract(alert, meter) -> result and notify(entries) can be injected (tests)."""
    from batchwatch.llm import MODELS, UsageMeter

    started, t0 = _now(), time.perf_counter()
    state = load_state()
    budget = state.setdefault("_budget", {})
    today = started[:10]
    if budget.get("day") != today:
        budget.update(day=today, usd=0.0)
    max_new = int(os.environ.get("WATCH_MAX_NEW_PER_RUN", 25))
    daily_usd = float(os.environ.get("WATCH_DAILY_USD", 0.5))

    entries = read_registry()
    known = {e["alert_id"] for e in entries}
    meter = UsageMeter()
    nano, sup = MODELS["nano"][0], MODELS["super"][0]
    llm = None

    def default_extract(alert, meter):
        nonlocal llm
        from batchwatch.extract import extract_alert
        from batchwatch.llm import make_client
        llm = llm or make_client()
        return extract_alert(llm, nano, alert, meter, sup)

    extract = extract or default_extract
    run = {"started": started, "dry_run": dry_run, "sources": [], "new": [], "deferred": 0, "failed": [], "out_of_credit": False}
    added = []

    with S._client() as c:
        for key, src in SOURCES.items():
            if only and key not in only:
                continue
            st = state.setdefault(key, {})
            first_look = "last_checked" not in st
            row = {"source": key, "name": src.name}
            s0 = time.perf_counter()
            try:
                items = src.list_new(c, per_source, st)
            except Exception as e:  # one regulator being down must not stop the others
                row.update(status="error", error=f"{type(e).__name__}: {str(e)[:160]}")
                st.pop("_staged", None)
                st["last_error"] = {"at": started, "error": row["error"]}
                run["sources"].append(row | {"ms": round((time.perf_counter() - s0) * 1000)})
                continue
            st["last_checked"] = started
            new = [i for i in items if i["alert_id"] not in known]
            row.update(listed=len(items), new=len(new), status="new" if new else "unchanged")
            if new:
                st["last_changed"] = started
            staged = st.pop("_staged", {})
            if dry_run:
                row["new_ids"] = [i["alert_id"] for i in new]
            else:
                missed = 0
                for item in new:
                    if run["out_of_credit"] or len(added) >= max_new or budget["usd"] + meter.cost_usd >= daily_usd:
                        run["deferred"] += 1  # still unknown, so the next run picks it up
                        missed += 1
                        continue
                    try:
                        alert = src.detail(c, item) if src.detail else item
                        result = extract(alert, meter)
                    except Exception as e:
                        run["failed"].append({"alert_id": item["alert_id"], "error": f"{type(e).__name__}: {str(e)[:160]}"})
                        missed += 1
                        continue
                    if result.get("error"):
                        from batchwatch.llm import out_of_credit
                        if out_of_credit(result["error"]):  # no point asking again this run; the alert waits
                            run["out_of_credit"] = True
                            run["deferred"] += 1
                        else:
                            run["failed"].append({"alert_id": item["alert_id"], "error": str(result["error"])[:160]})
                        missed += 1
                        continue
                    entry = to_entry(alert, result, detected_at=_now(), backfill=first_look)
                    added.append(entry)
                    known.add(entry["alert_id"])
                    st["last_new"] = entry["detected_at"]
                if not missed:
                    st.update(staged)
            run["sources"].append(row | {"ms": round((time.perf_counter() - s0) * 1000)})

    if added:
        write_registry(entries + added)
    budget["usd"] = round(budget["usd"] + meter.cost_usd, 4)
    run.update(new=[_summary(e) for e in added], finished=_now(), seconds=round(time.perf_counter() - t0, 1),
               cost_usd=round(meter.cost_usd, 4), calls_by_model=meter.calls_by_model, total_alerts=len(entries) + len(added))
    if added and notify is not False:
        from batchwatch.notify import send_all
        run["notified"] = (notify or send_all)([e for e in added if not e.get("backfill")])
    if not dry_run:  # a dry run must not move the source's state on, or the real run would miss what it saw
        save_state(state)
        append_run(run)
    return run


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*", choices=list(SOURCES))
    ap.add_argument("--per-source", type=int, default=PER_SOURCE)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    run = run_once(args.only, args.dry_run, args.per_source)
    for s in run["sources"]:
        extra = s.get("error") or (f"{s['new']} new of {s['listed']}" + (f": {', '.join(s['new_ids'][:5])}" if s.get("new_ids") else ""))
        print(f"{s['name']:<24} {s['status']:<10} {s['ms']:>6} ms  {extra}")
    print(json.dumps({k: run[k] for k in ("new", "deferred", "failed", "cost_usd", "seconds", "total_alerts")},
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
