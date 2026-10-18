"""Cross-border trace: an agent that finds where else a flagged product or batch has been reported.

Nemotron drives the investigation: it chooses the searches (Tavily search), chooses which pages to read
(Tavily extract), and writes the findings. Nothing it reports is shown as verified unless a deterministic
check finds its evidence quote, and every batch number it claims, in the text of a page it actually read.

Usage:
  python -m batchwatch.trace <alert_id>                      one alert from the registry
  python -m batchwatch.trace --all --max-usd 2 --max-tavily 550   every alert not traced yet, within a budget
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from batchwatch.extract import parse_json_reply
from batchwatch.llm import MODELS, UsageMeter, make_client
from batchwatch.tavily import Tavily

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "registry" / "traces"  # committed, so the deployed site shows them without re-running
MAX_STEPS = 8
PAGE_CHARS = 6000  # what the model sees of each page; verification uses the full text

SYSTEM = """You are BatchWatch's cross-border investigator. A drug regulator has flagged a medicine (and usually
specific batches). Find out where else in the world the same product or the same batches have been reported:
other national regulators, WHO, regional agencies (for example the African Medicines Agency) and credible news.

Work like this:
1. Search at least twice with different wording, for example the product with its batch numbers, and the product
   with "falsified" or "recall" and the manufacturer. Prefer official regulator and WHO pages.
2. Read the most promising pages with read_pages before you report them. Never report a page you did not read.
3. Stop when you have read the relevant pages or after a few searches turn up nothing new.

Then reply with JSON only, in this shape:
{
  "summary": "two or three plain sentences: where it has been reported, and anything that differs from the original alert",
  "findings": [
    {"country": "country or 'International'", "authority": "who published it", "url": "page you read",
     "date": "publication date if the page shows one, else null",
     "batch_numbers": ["batch numbers this page names, copied exactly"],
     "evidence_quote": "one sentence copied word for word from that page"}
  ]
}
Do not list the original alert itself as a finding. If nothing else was found, return an empty findings list."""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_web",
        "description": "Search the web. Returns titles, URLs and short snippets.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "search query"},
            "time_range": {"type": "string", "enum": ["month", "year"], "description": "optional recency filter"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_pages",
        "description": "Read up to 3 web pages (HTML or PDF) and return their text.",
        "parameters": {"type": "object", "properties": {
            "urls": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        }, "required": ["urls"]}}},
]


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _quote_found(quote: str, text: str) -> bool:
    """The quote, or at least a 60-character stretch of it, occurs in the page text."""
    q, t = _flat(quote), _flat(text)
    if len(q) < 20:
        return False
    if q in t:
        return True
    return any(q[i:i + 60] in t for i in range(0, max(1, len(q) - 60), 20))


def _batch_on_page(batch: str, text: str) -> bool:
    chars = [re.escape(c) for c in re.sub(r"[\s\-/.]", "", batch)]
    if not chars:
        return False
    pattern = r"(?<![A-Za-z0-9])" + r"[\s\-/.]{0,2}".join(chars) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, re.I) is not None


_DATE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y%m%d")


def parse_date(value) -> datetime | None:
    """Best-effort parse of the date formats regulators use ("14 April, 2026", "2026-08-21", "20260930")."""
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(value or "")).replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def add_timeline(findings: list[dict], original_date) -> None:
    """Days between each report and the original alert: negative means it was published earlier."""
    origin = parse_date(original_date)
    for f in findings:
        d = parse_date(f.get("date"))
        f["date_iso"] = d.date().isoformat() if d else None
        f["days_from_original"] = (d - origin).days if (d and origin) else None


def alert_brief(entry: dict) -> str:
    rec = entry.get("record") or {}
    lines = [f"Original alert: {entry.get('source')} ({entry.get('date')}): {entry.get('title')}", f"URL: {entry.get('url')}",
             f"Type: {rec.get('alert_type')}. Reason: {rec.get('reason')}"]
    for p in rec.get("products", []) or []:
        batches = ", ".join(b["batch_number"] for b in p.get("batches", []) if b.get("batch_number")) or "none given"
        lines.append(f"Product: {p.get('product_name')} | manufacturer: {p.get('manufacturer') or 'unknown'} | batches: {batches}")
    return "\n".join(lines)


def run_trace(entry: dict, model_key: str = "super", on_step=None) -> dict:
    """on_step(dict), if given, is called after every model decision and tool result, so a page can show the
    agent working live."""
    client, tavily, meter = make_client(), Tavily(), UsageMeter()
    emit = on_step or (lambda _event: None)
    model_id = MODELS[model_key][0]
    pages: dict[str, str] = {}  # url -> full text the agent read
    steps = []
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": alert_brief(entry)}]
    final_text = None

    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(model=model_id, temperature=0.2, max_tokens=6000,
                                              tools=TOOLS, tool_choice="auto", messages=messages)
        meter.add(resp.usage, model_id)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            final_text = msg.content
            emit({"event": "writing", "text": "Writing the report"})
            break
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [t.model_dump() for t in msg.tool_calls]})
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            if call.function.name == "search_web":
                emit({"event": "search", "query": args.get("query", "")})
                results = tavily.search(args.get("query", ""), time_range=args.get("time_range"))
                steps.append({"tool": "search", "query": args.get("query"), "results": len(results)})
                emit({"event": "results", "query": args.get("query", ""), "results": len(results),
                      "urls": [r["url"] for r in results[:4]]})
                payload = results
            else:
                urls = [u for u in args.get("urls", []) if isinstance(u, str)][:3]
                emit({"event": "read", "urls": urls})
                read = tavily.extract(urls)
                for p in read:
                    if p.get("content"):
                        pages[p["url"]] = p["content"]
                steps.append({"tool": "read", "urls": urls, "ok": sum(1 for p in read if p.get("content"))})
                emit({"event": "read_done", "urls": urls, "ok": sum(1 for p in read if p.get("content"))})
                payload = [{"url": p["url"], "text": p["content"][:PAGE_CHARS], "error": p.get("error")} for p in read]
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(payload, ensure_ascii=False)})
    else:
        messages.append({"role": "user", "content": "Stop searching now and reply with the JSON report."})
        resp = client.chat.completions.create(model=model_id, temperature=0, max_tokens=4000, messages=messages,
                                              response_format={"type": "json_object"})
        meter.add(resp.usage, model_id)
        final_text = resp.choices[0].message.content

    try:
        report = parse_json_reply(final_text or "{}")
    except (ValueError, json.JSONDecodeError):
        report = {"summary": "", "findings": []}

    original = (entry.get("url") or "").rstrip("/")
    findings = []
    for f in report.get("findings", []) or []:
        if not isinstance(f, dict):
            continue
        url = str(f.get("url") or "").rstrip("/")
        if url == original:
            continue
        text = next((t for u, t in pages.items() if u.rstrip("/") == url), None)
        claimed = [str(b) for b in (f.get("batch_numbers") or [])]
        f["read_by_agent"] = text is not None
        f["quote_verified"] = bool(text) and _quote_found(f.get("evidence_quote", ""), text)
        f["batches_verified"] = [b for b in claimed if text and _batch_on_page(b, text)]
        f["batches_unverified"] = [b for b in claimed if b not in f["batches_verified"]]
        f["verified"] = f["read_by_agent"] and f["quote_verified"] and not f["batches_unverified"]
        findings.append(f)
    add_timeline(findings, entry.get("date"))

    return {
        "alert_id": entry.get("alert_id"),
        "summary": report.get("summary", ""),
        "findings": sorted(findings, key=lambda f: (not f["verified"], str(f.get("date") or ""))),
        "steps": steps,
        "model": model_id,
        "tavily_calls": tavily.calls,
        "llm_cost_usd": round(meter.cost_usd, 4),
        "traced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def trace_path(alert_id: str) -> Path:
    return TRACES / (re.sub(r"[^A-Za-z0-9_.-]", "_", alert_id)[:120] + ".json")


def cached_trace(alert_id: str) -> dict | None:
    path = trace_path(alert_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def trace_cached(entry: dict, refresh: bool = False, on_step=None) -> dict:
    TRACES.mkdir(parents=True, exist_ok=True)
    path = trace_path(entry["alert_id"])
    if path.exists() and not refresh:
        result = json.loads(path.read_text(encoding="utf-8"))
        if "findings" in result and all("days_from_original" in f for f in result["findings"]):
            return result
        add_timeline(result.get("findings", []), entry.get("date"))  # older cache files predate the timeline
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    result = run_trace(entry, on_step=on_step)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def trace_all(entries: list[dict], max_usd: float, max_tavily: int, workers: int = 3) -> dict:
    """Trace every alert that has no saved trace yet, stopping before the model or Tavily budget runs out.
    Tavily bills one credit per basic search and per basic extract call of up to 5 pages."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    todo = [e for e in entries if not cached_trace(e["alert_id"])]
    spent = {"usd": 0.0, "tavily": 0, "done": 0, "failed": 0, "skipped": 0}
    lock = threading.Lock()

    def one(entry: dict) -> None:
        with lock:
            if spent["usd"] >= max_usd or spent["tavily"] >= max_tavily:
                spent["skipped"] += 1
                return
        try:
            r = trace_cached(entry)
        except Exception as e:  # one bad page must not stop the batch
            with lock:
                spent["failed"] += 1
            print(f"[failed] {entry['alert_id']}: {type(e).__name__}: {e}", flush=True)
            return
        with lock:
            spent["usd"] += r.get("llm_cost_usd", 0)
            spent["tavily"] += sum((r.get("tavily_calls") or {}).values())
            spent["done"] += 1
            verified = sum(1 for f in r["findings"] if f.get("verified"))
            print(f"[{spent['done']}/{len(todo)}] {entry['source']} {entry['alert_id'][:40]}: {len(r['findings'])} findings, "
                  f"{verified} verified · ${spent['usd']:.3f} · tavily {spent['tavily']}", flush=True)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, todo))
    spent["usd"] = round(spent["usd"], 4)
    return spent


def main() -> None:
    import argparse

    from dotenv import load_dotenv

    from batchwatch.pipeline import read_registry
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("alert_id", nargs="?")
    ap.add_argument("--all", action="store_true", help="trace every alert without a saved trace")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--max-tavily", type=int, default=550)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    if args.all:
        print(json.dumps(trace_all(read_registry(), args.max_usd, args.max_tavily, args.workers), indent=2))
        return
    entry = next(e for e in read_registry() if args.alert_id in e["alert_id"])
    print(json.dumps(trace_cached(entry, refresh=args.refresh), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
