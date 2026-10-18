"""Tell people about new alerts: Telegram, email (Resend), any webhook, and an Atom feed.

Each channel switches itself on when its settings are present in the environment, and off otherwise:
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID            a bot posts each new alert to a chat or channel
  RESEND_API_KEY + NOTIFY_EMAIL_TO + NOTIFY_EMAIL_FROM   one digest email per run (NOTIFY_EMAIL_TO is comma-separated)
  NOTIFY_WEBHOOK_URL                               a JSON POST per run (Slack-compatible "text" field included)
  NOTIFY_SOURCES                                   optional comma-separated filter, e.g. "SFDA (Saudi Arabia),WHO"
  SITE_URL                                         public address of the web app, used in links
Stock lists never leave the pharmacist's browser, so these messages are about alerts, not about anyone's stock;
the site itself re-checks a saved stock list when new alerts arrive.
"""
import html
import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import httpx

STOP_TYPES = {"recall", "falsified", "substandard", "watchlist"}
TYPE_WORD = {"recall": "Recall", "falsified": "Falsified medicine", "substandard": "Substandard medicine",
             "watchlist": "Watch list", "defect_notice": "Defect notice", "safety_communication": "Safety notice",
             "other": "Notice"}


def _site() -> str:
    return os.environ.get("SITE_URL", "").rstrip("/")


def describe(entry: dict) -> dict:
    """The facts every channel shows, in one place."""
    rec = entry.get("record") or {}
    products = rec.get("products") or []
    batches = [b.get("batch_number") for p in products for b in p.get("batches", []) if b.get("batch_number")]
    names = [p.get("product_name") for p in products if p.get("product_name")]
    kind = rec.get("alert_type") or "other"
    if rec.get("alert_status") == "withdrawn":
        word = "Alert withdrawn"
    else:
        word = TYPE_WORD.get(kind, "Notice")
    shown = ", ".join(("all batches" if b == "ALL" else b) for b in batches[:6]) + (f" (+{len(batches) - 6})" if len(batches) > 6 else "")
    return {
        "headline": f"{word} · {entry.get('source', '')}",
        "products": "; ".join(names[:3]) + (f" (+{len(names) - 3} more)" if len(names) > 3 else "") or entry.get("title", ""),
        "batches": shown or "no batch numbers given",
        "reason": rec.get("reason") or "",
        "action": rec.get("required_action") or "",
        "url": entry.get("url", ""),
        "stop": kind in STOP_TYPES and rec.get("alert_status") != "withdrawn",
        "date": entry.get("published_at") or entry.get("date", ""),
        "site_link": f"{_site()}/#alerts" if _site() else "",
    }


def _wanted(entries: list[dict]) -> list[dict]:
    allowed = {s.strip() for s in os.environ.get("NOTIFY_SOURCES", "").split(",") if s.strip()}
    return [e for e in entries if not allowed or e.get("source") in allowed]


def telegram(entries: list[dict], http: httpx.Client) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat and entries):
        return False
    for e in entries[:20]:
        d = describe(e)
        lines = [f"{'🔴' if d['stop'] else '🟠'} <b>{html.escape(d['headline'])}</b>",
                 html.escape(d["products"]), f"Batches: <code>{html.escape(d['batches'])}</code>"]
        if d["reason"]:
            lines.append(f"Reason: {html.escape(d['reason'])}")
        if d["action"]:
            lines.append(f"Regulator says: {html.escape(d['action'])}")
        lines.append(f'<a href="{html.escape(d["url"])}">Original notice</a>'
                     + (f' · <a href="{html.escape(d["site_link"])}">Check your stock</a>' if d["site_link"] else ""))
        r = http.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": "\n".join(lines), "parse_mode": "HTML", "disable_web_page_preview": True})
        r.raise_for_status()
    return True


def email(entries: list[dict], http: httpx.Client) -> bool:
    key, to, sender = os.environ.get("RESEND_API_KEY"), os.environ.get("NOTIFY_EMAIL_TO"), os.environ.get("NOTIFY_EMAIL_FROM")
    if not (key and to and sender and entries):
        return False
    rows = []
    for e in entries:
        d = describe(e)
        rows.append(f"""<tr><td style="padding:12px 0;border-bottom:1px solid #dde3e0">
          <div style="font-weight:700;color:{'#b42318' if d['stop'] else '#a04a06'}">{html.escape(d['headline'])}</div>
          <div style="font-weight:600">{html.escape(d['products'])}</div>
          <div>Batches: <code>{html.escape(d['batches'])}</code></div>
          {f"<div>Reason: {html.escape(d['reason'])}</div>" if d['reason'] else ""}
          {f"<div>Regulator says: {html.escape(d['action'])}</div>" if d['action'] else ""}
          <div><a href="{html.escape(d['url'])}">Original notice</a></div></td></tr>""")
    link = f'<p><a href="{html.escape(_site())}/#check">Check your stock on BatchWatch</a></p>' if _site() else ""
    body = (f"<div style=\"font-family:system-ui,sans-serif;max-width:640px\"><h2>{len(entries)} new medicine alert"
            f"{'s' if len(entries) != 1 else ''}</h2><table style=\"width:100%;border-collapse:collapse\">{''.join(rows)}</table>"
            f"{link}<p style=\"color:#66736e;font-size:13px\">BatchWatch reads regulator notices with NVIDIA Nemotron. "
            "Read the original notice before acting.</p></div>")
    r = http.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {key}"}, json={
        "from": sender, "to": [t.strip() for t in to.split(",") if t.strip()],
        "subject": f"BatchWatch: {len(entries)} new medicine alert{'s' if len(entries) != 1 else ''}", "html": body})
    r.raise_for_status()
    return True


def webhook(entries: list[dict], http: httpx.Client) -> bool:
    url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not (url and entries):
        return False
    items = [describe(e) | {"alert_id": e.get("alert_id")} for e in entries]
    text = "\n".join(f"{'🔴' if d['stop'] else '🟠'} {d['headline']}: {d['products']} · batches {d['batches']} · {d['url']}" for d in items)
    http.post(url, json={"text": text, "alerts": items}).raise_for_status()
    return True


def send_all(entries: list[dict]) -> list[str]:
    """Send new alerts on every configured channel. A failing channel is reported, never fatal."""
    entries = _wanted(entries)
    sent = []
    with httpx.Client(timeout=20) as http:
        for name, channel in (("telegram", telegram), ("email", email), ("webhook", webhook)):
            try:
                if channel(entries, http):
                    sent.append(name)
            except Exception as e:
                sent.append(f"{name} failed: {type(e).__name__}")
    return sent


def atom_feed(entries: list[dict], site_url: str = "", country_filter=None) -> str:
    """Newest alerts as an Atom feed any reader, pharmacy system or automation tool can subscribe to."""
    rows = [e for e in entries if not country_filter or country_filter(e)]
    rows.sort(key=lambda e: str(e.get("detected_at") or e.get("date") or ""), reverse=True)
    updated = rows[0].get("detected_at") if rows else datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
           "<title>BatchWatch: medicine recall and falsified-medicine alerts</title>",
           f"<id>{escape(site_url or 'urn:batchwatch')}/feed.xml</id>", f"<updated>{escape(str(updated))}</updated>",
           f'<link rel="alternate" href="{escape(site_url or "/")}"/>']
    for e in rows[:100]:
        d = describe(e)
        summary = f"{d['products']}. Batches: {d['batches']}." + (f" Reason: {d['reason']}." if d["reason"] else "") + \
                  (f" Regulator says: {d['action']}" if d["action"] else "")
        out += ["<entry>", f"<title>{escape(d['headline'] + ': ' + d['products'])}</title>",
                f"<id>urn:batchwatch:{escape(str(e.get('alert_id')))}</id>",
                f'<link href="{escape(d["url"])}"/>',
                f"<updated>{escape(str(e.get('detected_at') or d['date']))}</updated>",
                f"<summary>{escape(summary)}</summary>", "</entry>"]
    out.append("</feed>")
    return "\n".join(out)
