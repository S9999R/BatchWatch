"""Fetch recent medicine safety alerts from regulators that publish them through open APIs.

Each fetcher returns a list of alert dicts with the same keys:
source, alert_id, url, date, title, text
"""
import hashlib
import html
import re

import httpx

USER_AGENT = "BatchWatch/0.1 (medicine recall research prototype)"
MAX_TEXT_CHARS = 12_000  # keeps one alert well inside a cheap model call


def html_to_text(raw: str) -> str:
    """Flatten alert HTML to plain text while keeping table rows readable (batch tables live there)."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"\s+", " ", text)  # source newlines are layout noise; structure comes from the tags below
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"(?i)</tr>|<br\s*/?>|</p>|</li>|</h\d>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
    return text[:MAX_TEXT_CHARS]


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)


def fetch_fda(limit: int) -> list[dict]:
    """US FDA drug enforcement reports (openFDA). Lot numbers sit in the free-text code_info field."""
    with _client() as c:
        r = c.get("https://api.fda.gov/drug/enforcement.json",
                  params={"limit": limit, "sort": "report_date:desc"})
        r.raise_for_status()
    alerts = []
    for rec in r.json().get("results", []):
        text = "\n".join(
            f"{label}: {rec.get(key, '')}"
            for label, key in [
                ("Product", "product_description"),
                ("Lot / code info", "code_info"),
                ("Reason for recall", "reason_for_recall"),
                ("Recalling firm", "recalling_firm"),
                ("Classification", "classification"),
                ("Country", "country"),
                ("Distribution", "distribution_pattern"),
            ]
        )
        # Some enforcement records arrive without a recall number; fall back to a stable content hash.
        rid = rec.get("recall_number") or "FDA-" + hashlib.sha1(text.encode()).hexdigest()[:10]
        alerts.append({
            "source": "FDA (US)",
            "alert_id": rid,
            "url": f"https://api.fda.gov/drug/enforcement.json?search=recall_number:%22{rid}%22",
            "date": rec.get("report_date", ""),
            "title": rec.get("product_description", "")[:120],
            "text": text[:MAX_TEXT_CHARS],
        })
    return alerts


MHRA_MEDICINE_TITLE = re.compile(r"Medicines? (Recall|Defect)", re.I)


def list_mhra(c: httpx.Client, limit: int) -> list[dict]:
    """Newest MHRA medicine alerts from one GOV.UK search call: identity only, no notice text."""
    r = c.get("https://www.gov.uk/api/search.json", params={
        "filter_format": "medical_safety_alert",
        "count": limit * 4,  # the feed also contains device notices, which we skip
        "order": "-public_timestamp",
        "fields": "title,link,public_timestamp",
    })
    r.raise_for_status()
    items = [{"source": "MHRA (UK)", "alert_id": i["link"].rsplit("/", 1)[-1], "url": f"https://www.gov.uk{i['link']}",
              "link": i["link"], "date": i.get("public_timestamp", "")[:10], "published_at": i.get("public_timestamp", ""),
              "title": i.get("title", "")}
             for i in r.json().get("results", []) if MHRA_MEDICINE_TITLE.search(i.get("title", ""))]
    return items[:limit]


def detail_mhra(c: httpx.Client, item: dict) -> dict:
    page = c.get(f"https://www.gov.uk/api/content{item['link']}")
    page.raise_for_status()
    body = page.json().get("details", {}).get("body", "")
    return {**{k: v for k, v in item.items() if k != "link"}, "text": html_to_text(body)}


def fetch_mhra(limit: int) -> list[dict]:
    """UK MHRA medicine recalls and defect notices via the GOV.UK search and content APIs."""
    with _client() as c:
        return [detail_mhra(c, i) for i in list_mhra(c, limit)]


NAFDAC_RECALLS_CATEGORY = 29  # "Recalls and Safety Alerts" on nafdac.gov.ng


def fetch_nafdac(limit: int) -> list[dict]:
    """Nigeria NAFDAC public alerts via the site's WordPress REST API."""
    with _client() as c:
        r = c.get("https://nafdac.gov.ng/wp-json/wp/v2/posts", params={
            "categories": NAFDAC_RECALLS_CATEGORY,
            "per_page": limit,
            "_fields": "id,date,link,title,content",
        })
        r.raise_for_status()
    return [{
        "source": "NAFDAC (Nigeria)",
        "alert_id": str(p["id"]),
        "url": p["link"],
        "date": p["date"][:10],
        "title": html.unescape(p["title"]["rendered"]),
        "text": html_to_text(p["content"]["rendered"]),
    } for p in r.json()]


SFDA_BASE = "https://www.sfda.gov.sa"
SFDA_LABELS = {  # the notice pages label their fields in Arabic; English labels help every model and reader
    "التصنيف": "Audience", "التاريخ": "Date", "اسم المستحضر": "Product name", "نوع المستحضر": "Product type",
    "اجراء الاستدعاء": "Recall action", "مستوى الاستدعاء": "Recall level", "سبب السحب": "Reason for recall",
    "التوصيات": "Recommendations",
}


def sfda_notice_text(page_html: str) -> tuple[str, str, str]:
    """(title, date, text) of one SFDA drug circular / withdrawal notice."""
    node = re.search(r'(?is)<section[^>]*is-node--type-drugs-circulars-withdrawal.*?</section>', page_html)
    block = node.group(0) if node else page_html
    title = re.search(r"(?is)<h2[^>]*>(.*?)</h2>", block)
    title = html.unescape(re.sub(r"<[^>]+>|\s+", " ", title.group(1))).strip() if title else ""
    date = re.search(r"\b(20\d\d-\d\d-\d\d)\b", block)

    def label(m: re.Match) -> str:
        raw = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        return f"<br>{SFDA_LABELS.get(raw, raw)}: "  # <br> survives html_to_text's whitespace collapse

    block = re.sub(r'(?is)<div class="field__label">(.*?)</div>', label, block)
    block = re.sub(r"(?i)</div>", "<br>", block)
    text = html_to_text(block)
    field_date = re.search(r"(?m)^Date:\s*(20\d\d-\d\d-\d\d)", text)  # the circular's own date beats the page date
    return title, (field_date or date).group(1) if (field_date or date) else "", text


SFDA_MAX_PAGES = 40


def list_sfda(c: httpx.Client, limit: int) -> list[dict]:
    """Newest SFDA notice links, without opening the notices. The list page is not in date order (a June 2026
    recall sat on page 10 behind 2025 notices), so every page is read and the newest are picked by notice number,
    which grows with time."""
    items, seen, page = [], set(), 0
    while page < SFDA_MAX_PAGES:  # ponytail: ~20 pages exist today, 0.3 s each; raise the cap if SFDA grows past it
        resp = c.get(f"{SFDA_BASE}/en/drugs_circulars_withdrawal", params={"page": page})
        resp.raise_for_status()  # a failed page must not look like the end of the list
        listing = resp.text
        links = [l for l in dict.fromkeys(re.findall(r'href="(/en/drugs_?circulars_?(?:and_?)?withdrawal/\d+)"', listing))
                 if l not in seen]
        if not links:
            break
        for href in links:
            seen.add(href)
            items.append({"source": "SFDA (Saudi Arabia)", "alert_id": "SFDA-" + href.rsplit("/", 1)[-1],
                          "url": SFDA_BASE + href})
        page += 1
    items.sort(key=lambda i: int(i["alert_id"].split("-")[1]), reverse=True)
    return items[:limit]


def detail_sfda(c: httpx.Client, item: dict) -> dict:
    notice = c.get(item["url"])
    notice.raise_for_status()
    title, date, text = sfda_notice_text(notice.text)
    return {**item, "date": date, "title": title, "text": text}


def fetch_sfda(limit: int) -> list[dict]:
    """Saudi Food and Drug Authority: drug circulars and withdrawals (recall notices with batch numbers)."""
    with _client() as c:
        return [detail_sfda(c, i) for i in list_sfda(c, limit)]


HC_JSON = "https://recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json"
HC_ORGS = {"Drugs and health products", "Marketed health products"}


def health_canada_items(records: list[dict], limit: int) -> list[dict]:
    """Current drug recalls from the open-data index, newest first."""
    drugs = [r for r in records if r.get("Organization") in HC_ORGS and str(r.get("Archived")).lower() in ("0", "false", "")]
    drugs.sort(key=lambda r: r.get("Last updated", ""), reverse=True)
    return [{"source": "Health Canada", "alert_id": f"HC-{r.get('NID')}", "url": r["URL"],
             "date": str(r.get("Last updated", ""))[:10], "title": r.get("Title", "")} for r in drugs[:limit]]


def detail_health_canada(c: httpx.Client, item: dict) -> dict:
    page = c.get(item["url"])
    page.raise_for_status()
    main = re.search(r"(?is)<main.*?</main>", page.text)
    return {**item, "text": html_to_text(main.group(0) if main else page.text)}


def fetch_health_canada(limit: int) -> list[dict]:
    """Health Canada recalls and safety alerts: open-data index, then each notice page for lot numbers."""
    with _client() as c:
        records = c.get(HC_JSON, timeout=120).json()
        return [detail_health_canada(c, i) for i in health_canada_items(records, limit)]


RWANDA_URL = "https://monitoring.rwandafda.gov.rw/recalls/"


def fetch_rwanda(limit: int) -> list[dict]:
    """Rwanda FDA recall register: one table row per recall, with the batch number in its own column."""
    with _client() as c:
        page = c.get(RWANDA_URL).text
    cell = lambda s: html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()  # noqa: E731
    head = re.search(r"(?is)<thead.*?</thead>", page)
    headers = [cell(h) for h in re.findall(r"(?is)<th.*?</th>", head.group(0))] if head else []
    alerts = []
    for row in re.findall(r"(?is)<tr.*?</tr>", page):
        tds = re.findall(r"(?is)<td.*?</td>", row)
        if len(tds) < 8:
            continue
        values = [cell(t) for t in tds]
        fields = dict(zip(headers, values)) if len(headers) == len(values) else {}
        link = re.search(r'href="([^"]+)"', tds[-1])
        recall_no = values[1]
        day = re.match(r"(\d{2})/(\d{2})/(\d{4})", fields.get("Date of Recall", values[-2]))
        text = "\n".join(f"{k}: {v}" for k, v in fields.items() if k not in ("SN", "Details")) or " | ".join(values)
        slug = lambda s, n: re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:n]  # noqa: E731
        # One recall number can cover several products or batches, one row each, so the id includes both.
        rid = f"RW-{slug(recall_no, 40)}-{slug(values[2], 20)}-{slug(fields.get('Batch No.', values[4]), 16)}"
        alerts.append({"source": "Rwanda FDA", "alert_id": rid,
                       "url": link.group(1) if link and link.group(1).startswith("http") else RWANDA_URL,
                       "date": f"{day.group(3)}-{day.group(2)}-{day.group(1)}" if day else "",
                       "title": f"Recall {recall_no}: {values[2]}", "text": text})
        if len(alerts) >= limit:
            break
    return alerts


WHO_LIST = "https://www.who.int/teams/regulation-prequalification/incidents-and-SF/full-list-of-who-medical-product-alerts"


def who_items(listing: str, limit: int) -> list[dict]:
    """Newest WHO medical product alert links from the full-list page."""
    found = set(re.findall(r'href="(https://www\.who\.int/news/item/(\d\d)-(\d\d)-(20\d\d)-medical-product-alert[^"]*)"', listing))
    newest = sorted(found, key=lambda x: (x[3], x[2], x[1]), reverse=True)[:limit]
    return [{"source": "WHO", "alert_id": "WHO-" + url.rsplit("/", 1)[-1][:80], "url": url, "date": f"{yyyy}-{mm}-{dd}"}
            for url, dd, mm, yyyy in newest]


def detail_who(c: httpx.Client, item: dict) -> dict:
    page = c.get(item["url"])
    page.raise_for_status()
    body = re.search(r'(?is)<article.*?</article>', page.text) or re.search(r"(?is)<main.*?</main>", page.text)
    title = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page.text)
    return {**item, "title": html.unescape(re.sub(r"<[^>]+>|\s+", " ", title.group(1))).strip() if title else item["url"],
            "text": html_to_text(body.group(0) if body else page.text)}


def fetch_who(limit: int) -> list[dict]:
    """WHO Medical Product Alerts on substandard and falsified products (global)."""
    with _client() as c:
        return [detail_who(c, i) for i in who_items(c.get(WHO_LIST).text, limit)]


def _wordpress(base: str, source: str, prefix: str, category: int | None = None, search: str | None = None):
    """Fetcher for regulators that publish alerts as WordPress posts, by category and/or keyword search."""
    def fetch(limit: int) -> list[dict]:
        params = {"per_page": limit, "orderby": "date", "_fields": "id,date,link,title,content"}
        if category:
            params["categories"] = category
        if search:
            params["search"] = search
        with _client() as c:
            r = c.get(f"{base}/wp-json/wp/v2/posts", params=params)
            r.raise_for_status()
        return [{"source": source, "alert_id": f"{prefix}-{p['id']}", "url": p["link"], "date": p["date"][:10],
                 "title": html.unescape(re.sub(r"<[^>]+>", "", p["title"]["rendered"])).strip(),
                 "text": html_to_text(p["content"]["rendered"])} for p in r.json()]
    fetch.__doc__ = f"{source} alert posts via the site's WordPress REST API."
    return fetch


fetch_drap = _wordpress("https://www.dra.gov.pk", "DRAP (Pakistan)", "DRAP", category=149)  # "Recall Alerts"
# SAHPRA files recalls as press releases; Ghana's FDA files them across several categories.
fetch_sahpra = _wordpress("https://www.sahpra.org.za", "SAHPRA (South Africa)", "SAHPRA", category=113, search="recall")
fetch_fda_ghana = _wordpress("https://fdaghana.gov.gh", "FDA Ghana", "GH", search="batch")

FETCHERS = {"fda": fetch_fda, "mhra": fetch_mhra, "nafdac": fetch_nafdac, "sfda": fetch_sfda,
            "health_canada": fetch_health_canada, "drap": fetch_drap, "sahpra": fetch_sahpra,
            "rwanda": fetch_rwanda, "fda_ghana": fetch_fda_ghana, "who": fetch_who}
