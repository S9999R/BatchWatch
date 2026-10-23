"""Live search: when no stored alert names a batch, look for it in the regulator's own pages.

The watcher reads a regulator every 30 minutes and keeps a window of its newest alerts, so a recall it has not read
yet (or one outside the window) cannot be found in the registry. This module opens every SFDA notice and searches
its raw text for the batch number. It is a plain text search: no AI, no cost. Notices already in the registry are
searched from the text stored there, so only unread ones are downloaded. A hit is never a verdict, the pharmacist
reads the notice; no hit never means the batch is safe.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from batchwatch import sources as S
from batchwatch.match import norm_batch

SOURCE = "SFDA (Saudi Arabia)"
CACHE_SECONDS = 6 * 3600
DEADLINE_SECONDS = 60
WORKERS = 8
MIN_BATCH_CHARS = 3
_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9\-/.]*[A-Za-z0-9])?")

_lock = threading.Lock()
_listing: tuple[float, list[dict]] = (0.0, [])
_notices: dict[str, tuple[float, dict]] = {}  # ponytail: in-process cache, SFDA only ~200 notices; a shared cache if it must survive restarts


def find_batch(text: str, batch: str) -> str | None:
    """A short quote around the first token of text that is this batch number (spacing, dashes and case ignored)."""
    want = norm_batch(batch)
    for m in _TOKEN.finditer(text or ""):
        if norm_batch(m.group()) == want:
            return re.sub(r"\s+", " ", text[max(0, m.start() - 90):m.end() + 90]).strip()
    return None  # ponytail: a batch split by spaces ("BF 25 008") is not found; the stored-alert match still handles it


def _list(c) -> list[dict]:
    global _listing
    with _lock:
        if time.time() - _listing[0] > 1800:
            _listing = (time.time(), S.list_sfda(c, 10_000))
        return _listing[1]


def _notice(c, item: dict) -> dict:
    with _lock:
        hit = _notices.get(item["alert_id"])
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        alert = S.detail_sfda(c, item)
    except Exception:  # SFDA drops the odd request under parallel load; one more try before the notice counts as unread
        time.sleep(1)
        alert = S.detail_sfda(c, item)
    with _lock:
        _notices[item["alert_id"]] = (time.time(), alert)
    return alert


def search_sfda(batch: str, stored: dict[str, str]) -> dict:
    """Search every SFDA notice for the batch. stored maps alert_id to the notice text the registry already holds."""
    if len(norm_batch(batch)) < MIN_BATCH_CHARS:
        return {"regulator": SOURCE, "status": "skipped", "reason": "too_short"}
    out = {"regulator": SOURCE, "batch": batch, "listed": 0, "searched": 0, "from_registry": 0, "failed": 0, "hits": []}
    deadline = time.time() + DEADLINE_SECONDS
    try:
        with S._client() as c:
            items = _list(c)
            out["listed"] = len(items)

            def look(item):
                text = stored.get(item["alert_id"])
                if text is None:
                    if time.time() > deadline:
                        raise TimeoutError
                    item = _notice(c, item)
                    text = item.get("text", "")
                return item, text, item["alert_id"] in stored

            with ThreadPoolExecutor(WORKERS) as pool:
                futures = [pool.submit(look, i) for i in items]
                for f in futures:
                    try:
                        item, text, known = f.result()
                    except Exception:  # one unreadable notice must not hide the others, but the answer says it is incomplete
                        out["failed"] += 1
                        continue
                    out["searched"] += 1
                    out["from_registry"] += known
                    quote = find_batch(text, batch)
                    if quote:
                        out["hits"].append({"alert_id": item["alert_id"], "url": item["url"], "title": item.get("title", ""),
                                            "date": item.get("date", ""), "quote": quote, "in_registry": known})
    except Exception as e:
        out.update(status="error", reason=f"{type(e).__name__}")
        return out
    out["status"] = "found" if out["hits"] else "not_found"
    out["complete"] = out["failed"] == 0 and out["searched"] == out["listed"] > 0
    return out
