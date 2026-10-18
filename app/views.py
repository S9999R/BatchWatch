"""Shape registry rows, traces, runs and matches into the JSON the web page renders."""
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from batchwatch.llm import MODELS
from batchwatch.match import Match, confusable_key, norm_batch

ROOT = Path(__file__).resolve().parent.parent
MODEL_LABELS = {mid: f"Nemotron 3 {key.capitalize()}" for key, (mid, *_rest) in MODELS.items()}
MODEL_LABELS[MODELS["lightning"][0]] = "Nemotron 3.5 Lightning"
EVAL_RUNS = {"nano+super": "Nano, checked, Super when a check fails", "nano": "Nano alone, no fallback",
             "ultra": "Ultra alone, no fallback", "baseline_unguarded": "First unguarded run (45 alerts)"}

# Words that mark a regulator or public-health body, as opposed to a news site or tracking service.
_OFFICIAL = re.compile(r"agency|authority|board|ministry|department of health|drug office|administration|"
                       r"world health|\bwho\b|regulat|medicines|pharmacy and poisons|\bfda\b|\bmhra\b|bomra|nafdac|"
                       r"\bdrap\b|sahpra|health canada|sfda|nhs|famhp|aifa|pharmaceutical services",
                       re.I)


def model_label(model_id: str) -> str:
    return MODEL_LABELS.get(model_id or "", model_id or "unknown model")


def _iso(date: str) -> str:
    """FDA dates arrive as YYYYMMDD; the others as YYYY-MM-DD."""
    d = str(date or "")
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if re.fullmatch(r"\d{8}", d) else d


def checks_list(checks: dict) -> list[dict]:
    """The six checks behind every reading, in words a pharmacist (or a judge) can follow."""
    c = checks or {}
    grounding = c.get("grounding") or []
    return [
        {"name": "Every batch number appears in the notice", "passed": all(g.get("in_source") for g in grounding),
         "detail": f"{sum(1 for g in grounding if g.get('in_source'))} of {len(grounding)} found word for word"},
        {"name": "No batch number the rule-based reader saw was left out", "passed": not c.get("missed_candidates"),
         "detail": ", ".join(map(str, c.get("missed_candidates") or [])) or "none missed"},
        {"name": "Product names are spelled as in the notice", "passed": not c.get("name_words_not_in_source"),
         "detail": ", ".join(map(str, c.get("name_words_not_in_source") or [])) or "every word found"},
        {"name": "An all-batches recall is recorded as all batches", "passed": not c.get("all_batches_missing"),
         "detail": "checked against the notice wording"},
        {"name": "Each batch is filed under its own product", "passed": not c.get("misattached_batches"),
         "detail": ", ".join(map(str, c.get("misattached_batches") or [])) or "all in place"},
        {"name": "The model's reply was complete and valid", "passed": not c.get("error") and not c.get("broken_fragments"),
         "detail": str(c.get("error") or (f"{c['broken_fragments']} broken fragments dropped" if c.get("broken_fragments")
                                          else "constrained JSON, not cut off"))},
    ]


def check_flags(checks: dict) -> list[dict]:
    """Failed checks with the batch numbers they name, so a result can say which batch is in doubt."""
    c = checks or {}
    flags = []
    if c.get("misattached_batches"):
        flags.append({"check": "misattached", "batches": [str(b) for b in c["misattached_batches"]]})
    ungrounded = [g["batch_number"] for g in c.get("grounding") or [] if not g.get("in_source")]
    if ungrounded:
        flags.append({"check": "ungrounded", "batches": ungrounded})
    if c.get("missed_candidates"):
        flags.append({"check": "missed", "batches": [str(b) for b in c["missed_candidates"]]})
    if c.get("broken_fragments"):
        flags.append({"check": "fragments", "batches": []})
    if c.get("name_words_not_in_source"):
        flags.append({"check": "names", "batches": []})
    return flags


SOURCE_COUNTRY = {"SFDA (Saudi Arabia)": "Saudi Arabia", "FDA (US)": "United States", "MHRA (UK)": "United Kingdom",
                  "Health Canada": "Canada", "NAFDAC (Nigeria)": "Nigeria", "DRAP (Pakistan)": "Pakistan",
                  "SAHPRA (South Africa)": "South Africa", "Rwanda FDA": "Rwanda", "FDA Ghana": "Ghana", "WHO": ""}
_SHORT = {"UK": "United Kingdom", "US": "United States", "USA": "United States", "UAE": "United Arab Emirates"}


def clean_country(country: str | None) -> str:
    """'International (Hong Kong)' -> 'Hong Kong', 'Scotland (UK)' -> 'United Kingdom',
    'United States (California)' -> 'United States'."""
    c = str(country or "").strip()
    if "(" not in c:
        return _SHORT.get(c, c) or "International"
    base, inner = c.split("(", 1)
    base, inner = base.strip(), inner.rstrip(")").strip()
    if base.lower().startswith("international") or base.lower().startswith("multiple"):
        return _SHORT.get(inner, inner) or "International"
    return _SHORT.get(inner, _SHORT.get(base, base)) if inner.upper() in _SHORT else _SHORT.get(base, base)


def elsewhere(finding: dict, source: str) -> bool:
    """A verified report by a regulator in another country than the alert's own."""
    if not finding.get("verified") or source_kind(finding) != "regulator":
        return False
    home = SOURCE_COUNTRY.get(source, "")
    return not home or clean_country(finding.get("country")) != home


def source_kind(finding: dict) -> str:
    """Regulator or public-health body, judged by the publisher's name and the site's domain (never the page path,
    where a news article about a regulator would mention one)."""
    from urllib.parse import urlparse
    host = (urlparse(str(finding.get("url") or "")).hostname or "").lower()
    official_host = re.search(r"(^|\.)(gov|gob|go|govt|nic|admin)\.|\.gov$|\.gov\.|who\.int$|europa\.eu$|\.int$", host)
    return "regulator" if official_host or _OFFICIAL.search(str(finding.get("authority") or "")) else "other"


def trace_brief(trace: dict | None, source: str = "") -> dict | None:
    """What the agent found, in one line: which regulators in other countries reported it, verified on the page,
    and how far apart in time. News sites and same-country bodies are left out of the line (they stay in the trace)."""
    if not trace:
        return None
    found = [f for f in trace.get("findings", []) if elsewhere(f, source)]
    places = list(dict.fromkeys(clean_country(f.get("country")) for f in found))
    dated = [f for f in found if f.get("days_from_original") is not None]
    earliest = min(dated, key=lambda f: f["days_from_original"]) if dated else None
    return {"findings": len(trace.get("findings", [])), "verified": len(found), "places": places[:6],
            "earliest": {"country": clean_country(earliest.get("country")), "authority": earliest.get("authority"),
                         "days": earliest["days_from_original"]} if earliest else None,
            "traced_at": trace.get("traced_at")}


def decorate_trace(trace: dict | None, source: str = "") -> dict | None:
    if trace:
        for f in trace.get("findings", []):
            f["kind"] = source_kind(f)
            f["place"] = clean_country(f.get("country"))
            f["elsewhere"] = elsewhere(f, source)
    return trace


def alert_summary(entry: dict, trace: dict | None = None) -> dict:
    rec = entry.get("record") or {}
    checks = entry.get("checks") or {}
    products = []
    for p in rec.get("products", []) or []:
        products.append({
            "name": p.get("product_name") or "",
            "strength": p.get("strength_and_form") or "",
            "manufacturer": p.get("manufacturer") or "",
            "codes": [c for c in (p.get("product_codes") or []) if c],
            "batches": [{"number": b.get("batch_number"), "expiry": b.get("expiry_date"),
                         "added_by": b.get("added_by")} for b in p.get("batches", [])],
        })
    listed = checks_list(checks)
    return {
        "alert_id": entry.get("alert_id"),
        "source": entry.get("source"),
        "date": _iso(entry.get("date")),
        "published_at": entry.get("published_at") or _iso(entry.get("date")),
        "detected_at": entry.get("detected_at") or entry.get("extracted_at"),
        "backfill": entry.get("backfill", True),
        "title": entry.get("title"),
        "url": entry.get("url"),
        "type": (rec.get("alert_type") or "other").replace("_", " "),
        "status": rec.get("alert_status") or "active",
        "reason": rec.get("reason") or "",
        "action": rec.get("required_action") or "",
        "countries": rec.get("countries") or [],
        "products": products,
        "batch_count": sum(len(p["batches"]) for p in products),
        "model": model_label(checks.get("model_used") or entry.get("model")),
        "escalated_because": checks.get("escalated_because"),
        "checks": listed,
        "checks_passed": sum(1 for c in listed if c["passed"]),
        "flags": check_flags(checks),
        "has_text": bool(entry.get("text")),
        "trace": trace_brief(trace, entry.get("source", "")),
    }


def load_eval(name: str) -> dict | None:
    path = ROOT / "eval" / f"results_{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["summary"] if path.exists() else None


def registry_stats(entries: list[dict]) -> dict:
    by_source = defaultdict(lambda: {"alerts": 0, "batches": 0, "latest": ""})
    for e in entries:
        s = by_source[e.get("source", "?")]
        s["alerts"] += 1
        s["batches"] += sum(len(p.get("batches", [])) for p in (e.get("record") or {}).get("products", []) or [])
        s["latest"] = max(s["latest"], _iso(e.get("date", "")))
    models = []
    for key, label in EVAL_RUNS.items():
        ev = load_eval(key)
        if ev:
            models.append({"run": key, "label": label, **{k: ev.get(k) for k in (
                "alerts", "alerts_fully_correct", "batches_gold", "batches_correct", "batches_extra", "batches_missed",
                "product_attachment_correct", "escalated_to_fallback", "cost_usd", "median_latency_s", "calls_by_model")}})
    return {
        "alerts": len(entries),
        "batches": sum(s["batches"] for s in by_source.values()),
        "sources": [{"name": k, **v} for k, v in sorted(by_source.items())],
        "evaluation": load_eval("nano+super"),
        "evaluation_run": "U3 (9 Oct 2026, re-checked 11 Oct with the current rules)",
        "models": models,
    }


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value)
    try:
        if re.fullmatch(r"\d{8}", s):
            return datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def status(runs: list[dict], state: dict, entries: list[dict]) -> dict:
    """The watcher, as the page shows it: when each regulator was last checked and how fast new alerts arrive."""
    last = runs[-1] if runs else None
    now = datetime.now(timezone.utc)
    day_ago = [r for r in runs if (t := _parse_time(r.get("started"))) and (now - t).total_seconds() < 86400]
    lat = []
    for e in entries:
        if e.get("backfill", True):
            continue
        pub, seen = _parse_time(e.get("published_at") or e.get("date")), _parse_time(e.get("detected_at"))
        if pub and seen:
            lat.append(max(0.0, (seen - pub).total_seconds() / 3600))
    from batchwatch.watch import SOURCES
    sources = []
    for key, src in SOURCES.items():
        st = state.get(key) or {}
        sources.append({"key": key, "name": src.name, "last_checked": st.get("last_checked"),
                        "last_changed": st.get("last_changed"), "last_new": st.get("last_new"),
                        "last_error": (st.get("last_error") or {}).get("at") if (st.get("last_error") or {}).get("at", "") >= (st.get("last_checked") or "") else None})
    recent = sorted((e for e in entries if not e.get("backfill", True)), key=lambda e: e.get("detected_at") or "", reverse=True)
    return {
        "now": now.isoformat(timespec="seconds"),
        "watching": bool(runs),
        "last_run": {k: last.get(k) for k in ("started", "finished", "seconds", "cost_usd", "deferred")} | {
            "new": len(last.get("new", [])), "sources": last.get("sources", [])} if last else None,
        "runs_24h": len(day_ago),
        "new_24h": sum(len(r.get("new", [])) for r in day_ago),
        "cost_24h": round(sum(r.get("cost_usd") or 0 for r in day_ago), 4),
        "sources": sources,
        "detection": {"alerts": len(lat), "median_hours": round(statistics.median(lat), 1) if lat else None},
        "recent": [{"alert_id": e["alert_id"], "source": e.get("source"), "title": e.get("title"),
                    "detected_at": e.get("detected_at"), "published_at": e.get("published_at")} for e in recent[:10]],
    }


def insights(entries: list[dict], trace_of, only: set | None = None) -> dict:
    """What the cross-border agent found: how often a regulator in another country reported the same product,
    verified on the page, and how far apart the two regulators were when they named the same batches.
    `only` limits the count to a fixed set of alerts (the 90 of the evaluation), so the denominator never drifts."""
    traced = with_verified = 0
    gaps, places = [], defaultdict(int)
    for e in entries:
        if only is not None and e["alert_id"] not in only:
            continue
        t = trace_of(e["alert_id"])
        if not t:
            continue
        traced += 1
        verified = [f for f in t.get("findings", []) if elsewhere(f, e.get("source", ""))]
        if verified:
            with_verified += 1
        for f in verified:
            places[clean_country(f.get("country"))] += 1
            same_batches = [b for b in f.get("batches_verified", []) if str(b).upper() not in ("ALL", "ALL BATCHES")]
            if f.get("days_from_original") is not None and same_batches:  # the same batches, not just the same topic
                gaps.append({"days": f["days_from_original"], "from": e.get("source"), "to": f.get("authority"),
                             "country": clean_country(f.get("country")), "alert_id": e["alert_id"], "title": e.get("title"),
                             "batches": f.get("batches_verified", [])[:4]})
    spans = [abs(g["days"]) for g in gaps]  # same-day reports count: they are the common case (UK -> Hong Kong)
    return {
        "traced": traced,
        "with_verified": with_verified,
        "verified_reports": sum(places.values()),
        "places": sorted(places.items(), key=lambda kv: -kv[1])[:12],
        "gaps": len(spans),
        "median_gap_days": round(statistics.median(spans)) if spans else None,
        "within_day": sum(1 for d in spans if d <= 1),
        "within_week": sum(1 for d in spans if d <= 7),
        "month_or_more": sum(1 for d in spans if d >= 30),
        "longest": sorted(gaps, key=lambda g: -abs(g["days"]))[:6],
    }


def search(entries: list[dict], query: str, limit: int = 25) -> list[dict]:
    """Look up a batch number (exact or look-alike) or product words across the registry."""
    q = query.strip()
    if len(q) < 2:
        return []
    qb, qk = norm_batch(q), confusable_key(q)
    words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) >= 3]
    hits = []
    for e in entries:
        rec = e.get("record") or {}
        for p in rec.get("products", []) or []:
            name = f"{p.get('product_name') or ''} {p.get('manufacturer') or ''}".lower()
            for b in p.get("batches", []) or []:
                num = str(b.get("batch_number") or "")
                if num.upper() == "ALL":
                    continue
                if norm_batch(num) == qb:
                    hits.append(("exact batch", 0, e, p, num))
                elif len(qb) >= 4 and confusable_key(num) == qk:
                    hits.append(("look-alike batch", 1, e, p, num))
            if words and all(w in name for w in words):
                hits.append(("product name", 2, e, p, None))
    hits.sort(key=lambda h: (h[1], _iso(h[2].get("date", ""))), reverse=False)
    seen, out = set(), []
    for kind, _rank, e, p, num in hits:
        key = (e.get("alert_id"), p.get("product_name"), num)
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": kind, "batch": num, "product": p.get("product_name"),
                    "manufacturer": p.get("manufacturer"), "alert_id": e.get("alert_id")})
        if len(out) >= limit:
            break
    return out


def product_label(name: str, strength: str) -> str:
    """'Lorivan' + '2mg Tab' -> 'Lorivan (2mg Tab)'; no repeat when the name already carries the strength."""
    name, strength = (name or "").strip(), (strength or "").strip()
    if not strength or strength.lower() in name.lower():
        return name
    return f"{name} {strength}" if strength.startswith("(") else f"{name} ({strength})"


def match_json(m: Match, entry: dict | None = None, trace: dict | None = None) -> dict:
    a = m.alert
    checks = (entry or {}).get("checks") or {}
    listed = checks_list(checks)
    return {
        "tier": m.tier,
        "action": m.action,
        "reasons": m.reasons,
        "item": m.item,
        "alert": {"alert_id": a.alert_id, "source": a.source, "date": _iso(a.date), "title": a.title, "url": a.url,
                  "type": a.alert_type.replace("_", " "), "product": product_label(a.product_name, a.strength_and_form),
                  "batch": a.batch_number,
                  "condition": a.expiry_condition, "reason": a.reason, "required_action": a.required_action,
                  "manufacturer": a.manufacturer,
                  "model": model_label(checks.get("model_used")), "escalated_because": checks.get("escalated_because"),
                  "checks_passed": sum(1 for c in listed if c["passed"]), "checks_total": len(listed),
                  "flags": check_flags(checks), "trace": trace_brief(trace, a.source)},
    }
