"""Match extracted alert records against an institution's stock list.

Every match lands in one tier, and every tier carries the reasons that put it there:
  CONFIRMED  exact batch number and the same product (GTIN, or name plus manufacturer). Quarantine after a
             pharmacist approves; the system never quarantines on its own.
  VERIFY     plausible but not certain (look-alike batch characters, an "all batches" alert, a strength
             difference). Check the pack or ask the supplier.
  WITHDRAWN  the stock matches an alert the regulator has withdrawn. No action, shown for the audit trail.
  WATCH      the product is named in an alert that gives no batch numbers. Review.
A stock line with no match is not declared safe: it only means no covered alert names it.
"""
import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

CONFIRMED, VERIFY, WITHDRAWN, WATCH = "CONFIRMED", "VERIFY", "WITHDRAWN", "WATCH"
TIER_ORDER = {CONFIRMED: 0, VERIFY: 1, WATCH: 2, WITHDRAWN: 3}

SHORT_BATCH = 3  # batch numbers this short collide easily ("24"), so they need stronger product evidence

# ---------------------------------------------------------------- normalisation

def norm_batch(b) -> str:
    return re.sub(r"[\s\-/.]", "", str(b or "")).upper()


_CONFUSABLE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"})


def confusable_key(b) -> str:
    """Collapse characters that are easy to misread or mistype (O/0, I/1, S/5 ...)."""
    return norm_batch(b).translate(_CONFUSABLE)


def norm_gtin(g) -> str:
    digits = re.sub(r"\D", "", str(g or ""))
    return digits.lstrip("0")  # GTIN-13 and GTIN-14 of the same item differ only by leading zeros


_NAME_STOP = set("""tablet tablets tab tabs capsule capsules cap caps oral solution suspension powder for injection
infusion and solvent the of with film coated prolonged release sterile bp usp syrup cream ointment drop drops eye
in sachet sachets granules pre filled prefilled syringe ml mg g mcg iu per bottle bottles pack applicator fl oz
count rx only x each total lubricant medicines medicine product products brand concentrate ampoule ampoules amp
amps vial vials effervescent gel lotion spray inhaler patch patches suppository suppositories emulsion elixir
lozenge lozenges chewable dispersible orodispersible gastro resistant extended delayed modified""".split())

_CORP_STOP = set("""ltd limited pvt private inc plc gmbh ag co company llc sa s a m pharma pharmaceutical
pharmaceuticals laboratories laboratory labs lab industries the uk nigeria india healthcare group holdings b v
corp corporation international""".split())


def _words(s: str, stop: set) -> frozenset:
    return _word_set(str(s or ""), stop is _CORP_STOP)


@lru_cache(maxsize=100_000)  # the same names recur on every stock line and every alert
def _word_set(s: str, corp: bool) -> frozenset:
    stop = _CORP_STOP if corp else _NAME_STOP
    return frozenset(w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 1 and w not in stop)


def _brand(product_name: str) -> str:
    """The brand: the first distinctive word of the alert's product name (extraction puts the brand first)."""
    return next((w for w in re.findall(r"[a-z]+", str(product_name or "").lower())
                 if len(w) >= 4 and w not in _NAME_STOP), "")


@lru_cache(maxsize=200_000)
def _same_word(a: str, b: str) -> bool:
    """Equal, or a near-miss spelling of a longer word ("zentava" / "zentiva").
    The quick ratios are upper bounds of ratio(), so they only skip pairs that would fail anyway."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5:
        return False
    sm = SequenceMatcher(None, a, b)
    return sm.real_quick_ratio() >= 0.85 and sm.quick_ratio() >= 0.85 and sm.ratio() >= 0.85


def overlap(a: set, b: set) -> float:
    """Share of the smaller word set found in the larger one, tolerating near-miss spellings."""
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return sum(1 for w in small if w in large or any(_same_word(w, x) for x in large)) / len(small)


_STRENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|g|mcg|µg|ml|iu|%|million)\b", re.I)
_TO_MG = {"g": 1000.0, "mg": 1.0, "mcg": 0.001, "µg": 0.001}


def strengths(s: str) -> frozenset:
    """Strength and size tokens, with masses converted to mg so "1 g" equals "1000 mg"."""
    return _strength_set(str(s or ""))


@lru_cache(maxsize=100_000)
def _strength_set(s: str) -> frozenset:
    out = set()
    for value, unit in _STRENGTH.findall(s):
        unit = unit.lower()
        v = float(value) * _TO_MG.get(unit, 1.0)
        out.add((round(v, 4), "mg" if unit in _TO_MG else unit))
    return frozenset(out)


def parse_month(s) -> tuple | None:
    """(year, month) from MM/YYYY, DD/MM/YYYY, YYYY-MM or YYYY-MM-DD."""
    s = str(s or "")
    m = re.search(r"\b(\d{4})-(\d{1,2})(?:-\d{1,2})?\b", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\b(?:\d{1,2}/)?(\d{1,2})/(\d{4})\b", s)
    if m:
        return int(m.group(2)), int(m.group(1))
    return None


# ---------------------------------------------------------------- data

@dataclass
class AlertBatch:
    """One batch (or ALL) of one product in one alert."""
    alert_id: str
    source: str
    url: str
    date: str
    title: str
    alert_type: str
    alert_status: str
    product_name: str
    strength_and_form: str
    manufacturer: str
    product_codes: list
    batch_number: str
    expiry_condition: str
    reason: str
    required_action: str = ""


def alert_batches(extracted: list[dict]) -> list[AlertBatch]:
    """Flatten extracted alerts (as written by experiments/01 or eval) into one row per product batch.

    Products without batches are kept with batch_number "" so WATCH matches remain possible.
    """
    rows = []
    for a in extracted:
        rec = a.get("record") or {}
        if not rec:
            continue
        for p in rec.get("products", []) or []:
            batches = p.get("batches") or [{"batch_number": ""}]
            for b in batches:
                rows.append(AlertBatch(
                    alert_id=a.get("alert_id", ""), source=a.get("source", ""), url=a.get("url", ""),
                    date=a.get("date", ""), title=a.get("title", ""),
                    alert_type=(rec.get("alert_type") or "other").lower(),
                    alert_status=(rec.get("alert_status") or "active").lower(),
                    product_name=p.get("product_name") or "", strength_and_form=p.get("strength_and_form") or "",
                    manufacturer=p.get("manufacturer") or "",
                    product_codes=[str(c) for c in (p.get("product_codes") or [])],
                    batch_number=str(b.get("batch_number") or ""),
                    expiry_condition=str(b.get("expiry_date") or ""),
                    reason=rec.get("reason") or "",
                    required_action=rec.get("required_action") or "",
                ))
    return rows


INVENTORY_COLUMNS = {  # header names as pharmacy and hospital systems export them, English and Arabic
    "item_id": ["item_id", "id", "sku", "item", "code", "item_code", "product_code", "كود_الصنف", "الكود", "رمز_الصنف"],
    "product_name": ["product_name", "product", "name", "description", "item_name", "item_description", "drug_name",
                     "trade_name", "brand_name", "اسم_الصنف", "الصنف", "اسم_المنتج", "المنتج", "اسم_الدواء", "الاسم_التجاري"],
    "strength": ["strength", "dose", "strength_and_form", "التركيز", "القوة"],
    "manufacturer": ["manufacturer", "mah", "supplier_manufacturer", "maker", "company", "الشركة_المصنعة", "المصنع",
                     "الشركة"],
    "batch_number": ["batch_number", "batch", "lot", "lot_number", "batch_no", "lot_no", "batchno", "lotno",
                     "batch_lot", "رقم_التشغيلة", "التشغيلة", "رقم_الدفعة", "الدفعة", "رقم_الباتش"],
    "expiry": ["expiry", "expiry_date", "exp", "exp_date", "expiration", "expiration_date", "تاريخ_الانتهاء",
               "الانتهاء", "تاريخ_انتهاء_الصلاحية"],
    "gtin": ["gtin", "barcode", "ean", "gtin_code", "الباركود"],
    "quantity": ["quantity", "qty", "stock", "on_hand", "quantity_on_hand", "balance", "الكمية", "الرصيد"],
    "location": ["location", "store", "warehouse", "site", "branch", "shelf", "الموقع", "المستودع", "الفرع", "الرف"],
}
REQUIRED_COLUMNS = ("product_name", "batch_number")


def norm_header(h: str) -> str:
    """'Batch No.' -> 'batch_no', 'Exp. Date' -> 'exp_date', 'رقم التشغيلة' -> 'رقم_التشغيلة'."""
    return re.sub(r"[\W_]+", "_", str(h or "").replace("﻿", "").strip().lower()).strip("_")


def map_columns(headers: list[str]) -> dict:
    """Our field -> the file's header, from known header names."""
    by_norm = {norm_header(h): h for h in headers}
    mapping = {}
    for field_name, aliases in INVENTORY_COLUMNS.items():
        hit = next((by_norm[a] for a in aliases if a in by_norm), None)
        if hit and hit not in mapping.values():
            mapping[field_name] = hit
    return mapping


def read_rows(text: str) -> tuple[list[str], list[dict]]:
    """Headers and rows of a CSV, with , ; or tab separators."""
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    return list(reader.fieldnames or []), list(reader)


def parse_inventory(text: str, mapping: dict | None = None) -> list[dict]:
    """Parse stock CSV text, accepting common header names (Batch No., Lot, Qty, Arabic headers ...)."""
    return parse_inventory_columns(text, mapping)[0]


def parse_inventory_columns(text: str, mapping: dict | None = None) -> tuple[list[dict], dict]:
    """Like parse_inventory, and also returns which header was read as which field."""
    headers, rows = read_rows(text)
    mapping = mapping or map_columns(headers)
    missing = [f for f in REQUIRED_COLUMNS if f not in mapping]
    if missing:
        raise ValueError("The file needs a product name column and a batch number column "
                         f"(missing: {', '.join(missing)}). Download the template to see the expected headers.")
    items = []
    for i, row in enumerate(rows, 1):
        item = {k: (row.get(src) or "").strip() for k, src in mapping.items()}
        if not item.get("product_name") and not item.get("batch_number"):
            continue  # blank line
        item["item_id"] = item.get("item_id") or str(i)
        items.append(item)
    return items, mapping


def load_inventory(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return parse_inventory(f.read())


@dataclass
class Match:
    tier: str
    item: dict
    alert: AlertBatch
    reasons: list = field(default_factory=list)

    @property
    def action(self) -> str:
        """What to do. The tier says how sure the match is; the alert type says how serious the alert is.
        A defect notification (wrong leaflet, label error) asks for something different from a recall."""
        stop_use = self.alert.alert_type in STOP_USE_TYPES
        if self.tier == CONFIRMED:
            return ("Quarantine after pharmacist approval and contact the supplier" if stop_use
                    else "Your batch is named in this notice: follow the regulator's instruction")
        if self.tier == VERIFY:
            return ("Hold and confirm with the pack or supplier before use" if stop_use
                    else "Check the pack against the notice, then follow the regulator's instruction")
        if self.tier == WATCH:
            return "Review: product named in an alert without batch numbers"
        return "No action: the regulator withdrew this alert"


STOP_USE_TYPES = {"recall", "falsified", "substandard", "watchlist"}


# ---------------------------------------------------------------- matching

def _expiry_condition_ok(condition: str, stock_expiry: str) -> bool | None:
    """For ALL-batch alerts limited by expiry. True/False when decidable, None when not."""
    limit, exp = parse_month(condition), parse_month(stock_expiry)
    if not limit:
        return True  # unconditional ALL
    if not exp:
        return None
    c = condition.lower()
    if re.search(r"after|from|later than", c):
        return exp >= limit
    return exp <= limit  # "up to", "until", "before", "on or before" and bare dates


def compare(item: dict, ab: AlertBatch) -> Match | None:
    """Decide whether one stock line matches one alert batch, and how strongly."""
    reasons = []
    stock_batch = norm_batch(item.get("batch_number"))
    alert_batch = norm_batch(ab.batch_number)
    if (alert_batch and alert_batch != "ALL" and stock_batch != alert_batch
            and confusable_key(stock_batch) != confusable_key(alert_batch)):
        return None  # the batch rule below would reject it anyway; skip the costly name comparison

    # Product evidence
    alert_name = f"{ab.product_name} {ab.strength_and_form}"
    stock_name = f"{item.get('product_name', '')} {item.get('strength', '')}"
    name_score = overlap(_words(alert_name, _NAME_STOP), _words(stock_name, _NAME_STOP))
    s_alert, s_stock = strengths(alert_name), strengths(stock_name)
    strength_conflict = bool(s_alert and s_stock and not (s_alert & s_stock))

    alert_gtins = {norm_gtin(c) for c in ab.product_codes if len(re.sub(r"\D", "", c)) >= 12}
    stock_gtin = norm_gtin(item.get("gtin"))
    gtin_equal = bool(stock_gtin and stock_gtin in alert_gtins)
    gtin_conflict = bool(stock_gtin and alert_gtins and not gtin_equal)

    maker_score = overlap(_words(ab.manufacturer, _CORP_STOP), _words(item.get("manufacturer"), _CORP_STOP))
    maker_known = bool(_words(ab.manufacturer, _CORP_STOP) and _words(item.get("manufacturer"), _CORP_STOP))
    maker_conflict = maker_known and maker_score < 0.34
    maker_agree = maker_known and maker_score >= 0.5

    name_match = name_score >= 0.6
    brand = _brand(ab.product_name)
    brand_match = bool(brand) and any(_same_word(brand, w) for w in _words(stock_name, _NAME_STOP))
    if gtin_conflict:
        return None  # a different GTIN is a different product, whatever the batch says

    if gtin_equal:
        reasons.append("GTIN matches the alert")
    if name_match:
        reasons.append(f"product name matches ({name_score:.0%} of key words)")
    if maker_agree:
        reasons.append("manufacturer matches")
    same_product = gtin_equal or (name_match and not strength_conflict)

    # Batch-specific alert
    if alert_batch and alert_batch != "ALL":
        exact = stock_batch == alert_batch
        lookalike = not exact and confusable_key(stock_batch) == confusable_key(alert_batch)
        if not (exact or lookalike):
            return None
        if ab.alert_status == "withdrawn":
            ok = exact and (name_match or gtin_equal)
            why = [f"batch {ab.batch_number} matches exactly"] + reasons + ["regulator withdrew this alert"]
            return Match(WITHDRAWN, item, ab, why) if ok else None
        short = len(alert_batch) <= SHORT_BATCH
        # A stock list may describe the product differently ("KRABEVA concentrate" against "KRABEVA 400 mg per
        # 16 ml, Bevacizumab"). An exact, long batch on the alert's brand name is never dropped: a person checks it.
        brand_only = exact and not short and brand_match and not (name_match or gtin_equal)
        if not (name_match or gtin_equal or brand_only):
            return None  # the same number on an unrelated product is a coincidence, not a match
        if maker_conflict and not gtin_equal:
            # Alerts often name the recalling firm while stock lists the distributor, so a long, exact batch on a
            # strongly matching name is still surfaced. A short number with a different maker is a coincidence.
            if exact and not short and name_score >= 0.8 and not strength_conflict:
                return Match(VERIFY, item, ab, [f"batch {ab.batch_number} matches exactly"] + reasons + [
                    f"manufacturer differs: alert names {ab.manufacturer!r}, stock lists {item.get('manufacturer')!r}"])
            return None
        if exact:
            reasons.insert(0, f"batch {ab.batch_number} matches exactly")
            if brand_only:
                reasons.append(f"brand name {brand.upper()} matches, but the rest of the product description differs "
                               "from the alert: check the pack")
            if strength_conflict and not gtin_equal:
                return Match(VERIFY, item, ab, reasons + ["strength on the stock line differs from the alert"])
            if same_product and (not short or gtin_equal or maker_agree):
                return Match(CONFIRMED, item, ab, reasons)
            return Match(VERIFY, item, ab, reasons + (["short batch number, product evidence is not strong enough to confirm"] if short else []))
        reasons.insert(0, f"batch {item.get('batch_number')} differs from {ab.batch_number} only by look-alike characters")
        return Match(VERIFY, item, ab, reasons) if same_product else None

    # Alert that covers every batch of a product
    if alert_batch == "ALL":
        if ab.alert_status == "withdrawn" or not same_product or (maker_conflict and not gtin_equal):
            return None
        ok = _expiry_condition_ok(ab.expiry_condition, item.get("expiry"))
        if ok is False:
            return None
        reasons.insert(0, "alert covers all batches" + (f" ({ab.expiry_condition})" if ab.expiry_condition else ""))
        if ok is None:
            reasons.append("stock expiry unknown, cannot check the alert's expiry condition")
        return Match(CONFIRMED if gtin_equal and ok else VERIFY, item, ab, reasons)

    # Alert that names a product but no batches
    if ab.alert_type in ("safety_communication", "other") or ab.alert_status == "withdrawn":
        return None
    if name_match and not strength_conflict and not maker_conflict:
        return Match(WATCH, item, ab, reasons)
    return None


def lookup_pack(extracted: list[dict], batch: str, gtin: str = "", product_name: str = "", expiry: str = "") -> list[Match]:
    """Check one pack, as read from its barcode or typed in. A barcode carries GTIN and batch but no name, so a
    batch-only hit is VERIFY with the alert's product shown for the pharmacist to compare with the pack."""
    item = {"item_id": "pack", "product_name": product_name, "batch_number": batch, "gtin": gtin,
            "expiry": expiry, "manufacturer": ""}
    nb, ng = norm_batch(batch), norm_gtin(gtin)
    best: dict[str, Match] = {}

    def keep(m: Match) -> None:
        old = best.get(m.alert.alert_id)
        if old is None or TIER_ORDER[m.tier] < TIER_ORDER[old.tier]:
            best[m.alert.alert_id] = m

    for ab in alert_batches(extracted):
        if product_name:
            m = compare(item, ab)
            if m:
                keep(m)
                continue
        alert_gtins = {norm_gtin(c) for c in ab.product_codes if len(re.sub(r"\D", "", c)) >= 12}
        gtin_equal = bool(ng and ng in alert_gtins)
        if ng and alert_gtins and not gtin_equal:
            continue  # the alert names a different GTIN: a different product
        ab_batch = norm_batch(ab.batch_number)
        if ab_batch == "ALL":
            if gtin_equal and ab.alert_status != "withdrawn" and _expiry_condition_ok(ab.expiry_condition, expiry) is not False:
                keep(Match(VERIFY, item, ab, ["GTIN matches a product recalled in all batches"
                                              + (f" ({ab.expiry_condition})" if ab.expiry_condition else "")]))
            continue
        if not ab_batch or not nb:
            continue
        exact = ab_batch == nb
        lookalike = not exact and len(nb) >= 4 and confusable_key(nb) == confusable_key(ab_batch)
        if not (exact or lookalike):
            continue
        strength = ab.strength_and_form if ab.strength_and_form.lower() not in ab.product_name.lower() else ""
        product = f"{ab.product_name} {strength}".strip()
        if ab.alert_status == "withdrawn":
            if exact:
                keep(Match(WITHDRAWN, item, ab, [f"batch {ab.batch_number} matches", "regulator withdrew this alert"]))
            continue
        if exact and gtin_equal:
            keep(Match(CONFIRMED, item, ab, [f"batch {ab.batch_number} matches exactly", "GTIN matches the alert"]))
        elif exact and len(ab_batch) > SHORT_BATCH:
            keep(Match(VERIFY, item, ab, [f"batch {ab.batch_number} matches exactly",
                                          f"the alert is about {product}: check that your pack is this product"]))
        elif lookalike and (gtin_equal or len(ab_batch) > SHORT_BATCH):
            keep(Match(VERIFY, item, ab, [f"batch {batch} differs from {ab.batch_number} only by look-alike characters",
                                          f"the alert is about {product}"]))
    return sorted(best.values(), key=lambda m: TIER_ORDER[m.tier])


def match_inventory(items: list[dict], extracted: list[dict]) -> list[Match]:
    """All matches, strongest first. One stock line keeps only its strongest match per alert."""
    batches = alert_batches(extracted)
    # A batch-specific alert can only match a stock line whose batch is equal or look-alike, which means the
    # same confusable key; index them by it. "ALL" and batch-less alerts are compared with every line.
    by_key, general = defaultdict(list), []
    for n, ab in enumerate(batches):
        b = norm_batch(ab.batch_number)
        (by_key[confusable_key(b)] if b and b != "ALL" else general).append((n, ab))
    best = {}
    for item in items:
        candidates = sorted(by_key.get(confusable_key(item.get("batch_number")), []) + general, key=lambda c: c[0])
        for _n, ab in candidates:
            m = compare(item, ab)
            if m is None:
                continue
            key = (item["item_id"], ab.alert_id)
            if key not in best or TIER_ORDER[m.tier] < TIER_ORDER[best[key].tier]:
                best[key] = m
    return sorted(best.values(), key=lambda m: (TIER_ORDER[m.tier], m.item.get("location", ""), m.item["item_id"]))
