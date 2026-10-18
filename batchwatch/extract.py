"""Turn one regulator alert into structured, batch-level records with Nemotron."""
import json
import re

SYSTEM_PROMPT = """You extract structured data from official medicine safety alerts (recalls, substandard or falsified product alerts) published by drug regulators.

Return ONLY a JSON object, with no prose and no markdown, in exactly this shape:
{
  "alert_type": "recall" | "substandard" | "falsified" | "watchlist" | "defect_notice" | "safety_communication" | "other",
  "alert_status": "active" | "withdrawn",
  "products": [
    {
      "product_name": string,
      "strength_and_form": string or null,
      "manufacturer": string or null,
      "product_codes": [string],
      "batches": [{"batch_number": string, "expiry_date": string or null}]
    }
  ],
  "reason": string,
  "countries": [string],
  "required_action": string or null
}

Rules:
- Copy batch and lot numbers exactly as written in the alert. Never invent, guess or reformat them.
- batch_number holds only the identifier itself: leave out words and symbols such as "Lot", "Batch", "No.", "BN" or "#".
- Give every batch its own entry, even when the alert lists several in one sentence.
- If the alert says every batch or lot of a product is affected, use one batch with batch_number "ALL".
  If it says all batches up to or after some expiry date, use "ALL" and put that condition in expiry_date.
- If the alert gives no batch numbers and does not say all batches are affected, return an empty "batches" list.
- product_name must identify one specific product including its strength and form exactly as written
  (for example "Xaggitin XL 18 mg Prolonged-release Tablets"), so products of different strengths stay distinct.
  When the alert gives a brand (trade) name, product_name starts with it ("GAMMARAAS Injection"), because stock
  lists are kept by brand; put the generic description in strength_and_form.
- If the alert lists affected batch numbers, it concerns a product defect (including leaflet, label or packaging
  errors): list those batches, whatever the alert_type.
- A safety communication about how a medicine is used (interactions, dosing, side effects) that names no
  batches is not a product defect: set alert_type "safety_communication", leave "batches" empty and do not use "ALL".
- Some tables arrive flattened into one line: a header row, then the values in the same column order. Map each
  value to its column by that order. For example after "Trade Name Scientific Name Register Number Affected batch
  number Manufacturer", the value after the register number (or after "غير مسجل"/"Unregistered", which fills the
  register column) is the batch number, and further batch numbers can follow the manufacturer.
- If the notice says an earlier alert was issued in error, cancelled or resolved, set alert_status "withdrawn"
  and still list the batches it refers to. Otherwise alert_status is "active".
- product_codes holds identifiers such as GTIN, NDC, PL or registration numbers, copied exactly.
- The alert may be in any language. Write descriptive fields in English; keep batch numbers and codes as written."""


def parse_json_reply(content: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating a reasoning block or code fences."""
    content = re.sub(r"(?s)<think>.*?</think>", "", content or "")
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    return json.loads(content[start:end + 1])


BATCH_LABEL = re.compile(r"(?i)^\s*(?:(?:lot|batch|bn|b/n)\b\s*(?:no\b\.?|number\b)?\s*[:#.\-]?\s*|#\s*:?\s*)+")
BATCH_SPLIT = re.compile(r"\s*(?:,|;|&|\band\b)\s*", re.I)


ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")


def clean_batch_numbers(raw: str) -> list[str]:
    """Strip labels the model sometimes keeps ("Lot# 3161" -> "3161"), split accidental lists, close up spaces
    around separators ("2604- 54TL3" -> "2604-54TL3"), and drop Arabic words: a batch number is printed in Latin
    letters and digits, so Arabic text in that field is a phrase from the notice ("مرفق بالتعميم", "attached")."""
    out = []
    for part in BATCH_SPLIT.split(str(raw or "")):
        part = BATCH_LABEL.sub("", part).strip(" .:;,")
        part = re.sub(r"\s*([\-/])\s*", r"\1", part)
        if part and not ARABIC_SCRIPT.search(part):
            out.append(part)
    return out


FIELD_NAMES = {"alert_type", "alert_status", "products", "product_name", "strength_and_form", "manufacturer",
               "product_codes", "batches", "batch_number", "expiry_date", "reason", "countries", "required_action"}
FIELD_FRAGMENT = re.compile(r'(?i)^\s*"?(?:' + "|".join(sorted(FIELD_NAMES)) + r')"?\s*(?::|$)|":|[{}]')


def is_fragment(name) -> bool:
    """A product name that is really a piece of a broken reply ('reason', 'alert_type": ...', '{...}')."""
    n = str(name or "").strip()
    return not n or n.lower().strip('" ,') in FIELD_NAMES or bool(FIELD_FRAGMENT.search(n))


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def normalise_record(record: dict) -> dict:
    """Coerce the reply into the expected shape and clean every batch number.

    Models occasionally return a product as a bare string or batches as bare strings; both are accepted.
    The model's original batch string is kept as raw_batch_number for traceability.
    """
    if not isinstance(record, dict):
        raise ValueError("model reply is not a JSON object")
    products, fragments = [], 0
    for product in _as_list(record.get("products")):
        if isinstance(product, str):
            product = {"product_name": product}
        if not isinstance(product, dict):
            continue
        if is_fragment(product.get("product_name")):  # the model's own broken JSON, written into a name field
            fragments += 1
            continue
        cleaned = []
        for batch in _as_list(product.get("batches")):
            if not isinstance(batch, dict):
                batch = {"batch_number": batch, "expiry_date": None}
            raw = str(batch.get("batch_number") or "")
            for number in clean_batch_numbers(raw):
                cleaned.append({**batch, "batch_number": number, "raw_batch_number": raw})
        products.append({**product, "batches": cleaned})
    record["products"] = products
    record["broken_fragments"] = fragments
    return record


def appears_in(token: str, text: str) -> bool:
    """True if token occurs in text as a whole identifier, tolerating spaces, hyphens or slashes inside it.

    The alphanumeric boundaries matter: batch "24" must not count as found inside "2024" or "24L069".
    """
    chars = [re.escape(c) for c in re.sub(r"[\s\-/.]", "", token)]
    if not chars:
        return False
    pattern = r"(?<![A-Za-z0-9])" + r"[\s\-/.]{0,2}".join(chars) + r"(?![A-Za-z0-9])"  # "2604- 54TL3" in a table
    return re.search(pattern, text, re.I) is not None


def check_grounding(record: dict, source_text: str) -> list[dict]:
    """Flag every extracted batch number that does not appear in the alert text as a whole identifier.

    A batch that is not in the source is treated as a possible hallucination and never auto-matched.
    """
    checks = []
    for product in record.get("products", []) or []:
        for batch in product.get("batches", []) or []:
            number = str(batch.get("batch_number", "")).strip()
            if not number:
                continue
            found = number == "ALL" or appears_in(number, source_text)
            checks.append({"product": product.get("product_name"), "batch_number": number, "in_source": found})
    return checks


LABELLED_BATCH = re.compile(
    r"(?i)\b(?:batch|lot)\s*(?:no\b\.?|number\b|#)?\s*[:#.]?\s*\|?\s*([A-Za-z0-9][A-Za-z0-9\-/]{1,24})")
TABLE_HEADER_CELL = re.compile(r"(?i)^(?:affected\s+)?(?:batch|lot)(?:\s*(?:no\.?|number|numbers|#))?$")
DATE_LIKE = re.compile(r"^\d{1,4}[/\-.]\d{1,2}([/\-.]\d{2,4})?$")


def _looks_like_batch(token: str) -> bool:
    t = token.strip(" .,;:-")
    return (2 <= len(t) <= 24 and any(c.isdigit() for c in t) and not DATE_LIKE.match(t)
            and not re.fullmatch(r"\d{13,14}", t)  # a bare 13-14 digit number is a GTIN, not a batch
            and not re.search(r"[A-Za-z]{4,}\s|\s[A-Za-z]{4,}|[*()]", t))  # prose such as "including 05/2029*"


FLAT_HEADER = re.compile(r"(?i)register number\s+affected batch numbers?\s+manufacturer")
STRENGTH_TEXT = re.compile(r"(?i)\(?\b(?:equ\.?\s*)?\d+(?:[.,/]\d+)*\s*(?:mg|g|gm|ml|mcg|iu|%)\b\)?(?:\s*/\s*\d*(?:\.\d+)?\s*(?:mg|g|ml)\b)?")


def flattened_row_batches(text: str) -> set[str]:
    """SFDA-style notices flatten their table into one line after a fixed English header. In that row, the
    batch-like tokens are the batches: strengths, 10-digit registration numbers and date-like codes are removed."""
    found = set()
    for m in FLAT_HEADER.finditer(text):
        row = text[m.end():].split("\n", 1)[0]
        row = STRENGTH_TEXT.sub(" ", row)
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,19}", row):
            if any(ch.isdigit() for ch in token) and not re.fullmatch(r"\d{10}", token) and not DATE_LIKE.match(token):
                found.add(token)
    return found


def split_row_batches(text: str) -> set[str]:
    """Tables whose cells each sit on their own line ("Tablet Nitras\\n| Glyceryl Trinitrate\\n| 24L069\\n|...")
    with an empty cell closing each row. Rows are rebuilt from the cell sequence; the batch column is the
    header cell that reads "Batch No." and only rows with the header's width are trusted."""
    found = set()
    groups, current, pos = [], [], 0
    for m in re.finditer(r"\|", text):
        cell = text[pos:m.start()]
        if not current:  # a row's first cell may carry the prose before the table: keep its last line only
            lines = [l for l in cell.split("\n") if l.strip()]
            cell = lines[-1] if lines else ""
        cell = " ".join(cell.split())
        if cell:
            current.append(cell)
        line_end = text.find("\n", m.end())
        if not text[m.end(): line_end if line_end != -1 else len(text)].strip():  # "|" closing a line ends the row
            if current:
                groups.append(current)
            current = []
        pos = m.end()
    width = col = None
    for g in groups:
        header = next((i for i, c in enumerate(g) if TABLE_HEADER_CELL.match(c)), None)
        if header is not None and len(g) >= 3:
            width, col = len(g), header
        elif width and len(g) == width:
            for number in clean_batch_numbers(g[col]):
                parts = number.split()
                if len(parts) > 1 and all(_looks_like_batch(p) for p in parts):
                    found.update(parts)  # several lots in one cell, one per line ("650920\n650923\n...")
                elif _looks_like_batch(number):
                    found.add(number)
    return found


def candidate_batches(text: str) -> set[str]:
    """Batch numbers a deterministic reader can see without the model: values right after a "Batch No."/"Lot"
    label, cells under a batch column in table rows (one line per row or one line per cell), and values in a
    flattened SFDA-style row. Used only to catch numbers the model missed, so it may under-report but should
    not invent."""
    found = {m.group(1).strip(" .,;:-") for m in LABELLED_BATCH.finditer(text) if _looks_like_batch(m.group(1))}
    found |= flattened_row_batches(text)
    found |= split_row_batches(text)
    col = None
    for line in text.split("\n"):
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:  # not a single-line table row
            col = None
            continue
        header = next((i for i, c in enumerate(cells) if TABLE_HEADER_CELL.match(c)), None)
        if header is not None:
            col = header
        elif col is not None and col < len(cells) and _looks_like_batch(cells[col]):
            found.add(cells[col].strip(" .,;:-"))
    return found


def missed_candidates(record: dict, source_text: str) -> list[str]:
    """Candidate batches that the model's record does not contain."""
    got = {re.sub(r"[\s\-/.]", "", str(b.get("batch_number", ""))).upper()
           for p in (record or {}).get("products", []) or [] for b in p.get("batches", []) or []}
    return sorted(c for c in candidate_batches(source_text) if re.sub(r"[\s\-/.]", "", c).upper() not in got)


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def misattached_batches(record: dict, source_text: str) -> list[str]:
    """In multi-product alerts, batches the model filed under a different product than the one whose name
    appears closest above the batch in the text. Only checked when every product name occurs verbatim."""
    products = [p for p in (record or {}).get("products", []) or [] if p.get("batches")]
    if len(products) < 2:
        return []
    flat = _flat(source_text)
    name_positions = []
    for p in products:
        name = _flat(p.get("product_name"))
        positions = [m.start() for m in re.finditer(re.escape(name), flat)] if len(name) >= 6 else []
        if not positions:
            return []  # names were paraphrased, so this check cannot judge the alert
        name_positions.append(positions)
    wrong = []
    for idx, p in enumerate(products):
        for b in p["batches"]:
            number = str(b.get("batch_number", ""))
            if number.upper() == "ALL":
                continue
            hit = re.search(r"(?<![a-z0-9])" + re.escape(number.lower()) + r"(?![a-z0-9])", flat)
            if not hit:
                continue
            nearest = [(max((q for q in qs if q < hit.start()), default=-1), i) for i, qs in enumerate(name_positions)]
            pos, owner = max(nearest)
            if pos >= 0 and owner != idx:
                wrong.append(number)
    return wrong


ALL_BATCHES_PHRASE = re.compile(r"(?i)\ball\s+(?:the\s+)?(?:batches|lots|batch numbers)\b")


RECALL_CONTEXT = re.compile(r"(?i)recall|withdraw|affected|banned|suspend|falsified|substandard|not (?:be )?(?:use|sold)")


def says_all_batches_affected(text: str) -> bool:
    """True if "all batches/lots" appears as a recall statement: in a recall sentence, or as a table/field value
    ("Batch No.: All batches"). A rule such as "all batches of imported glycerin need a certificate" does not count."""
    for m in ALL_BATCHES_PHRASE.finditer(text):
        line_start, line_end = text.rfind("\n", 0, m.start()) + 1, text.find("\n", m.end())
        line = text[line_start: line_end if line_end != -1 else len(text)]
        as_value = (re.fullmatch(r"(?i)\s*(?:[\w ./]{0,30}:\s*)?\|?\s*all\s+(?:the\s+)?(?:batches|lots)\b.{0,60}", line)
                    or re.search(r"(?i)\|\s*all\s+(?:the\s+)?(?:batches|lots)\s*(?:\||$)", line))  # a table cell
        window = text[max(0, m.start() - 120): m.end() + 120]
        if as_value or RECALL_CONTEXT.search(window):
            return True
    return False


def drop_unsupported_all(record: dict, source_text: str) -> bool:
    """Remove ALL entries the text does not back up, and report whether any were removed.

    "All batches" flags every unit of a product in stock, so it needs the regulator's own words. Without them the
    product stays listed with no batches, which the matcher shows as WATCH for a person to review."""
    if says_all_batches_affected(source_text):
        return False
    removed = False
    for p in (record or {}).get("products", []) or []:
        kept = [b for b in p.get("batches", []) or [] if str(b.get("batch_number", "")).upper() != "ALL"]
        if len(kept) != len(p.get("batches", []) or []):
            p["batches"], removed = kept, True
    return removed


def all_batches_missing(record: dict, source_text: str) -> bool:
    """The alert says all batches/lots are affected, but the answer has no ALL entry."""
    if not says_all_batches_affected(source_text):
        return False
    return not any(str(b.get("batch_number", "")).upper() == "ALL"
                   for p in (record or {}).get("products", []) or [] for b in p.get("batches", []) or [])


def ungrounded_name_words(record: dict, source_text: str) -> list[str]:
    """Words of four or more letters in product names that never occur in the alert text.

    Catches misspelled names ("Zentava" for "Zentiva"), which would otherwise slip past matching."""
    text_words = set(re.findall(r"[a-z]{4,}", source_text.lower()))
    missing = []
    for p in (record or {}).get("products", []) or []:
        for w in re.findall(r"[A-Za-z]{4,}", str(p.get("product_name") or "")):
            if w.lower() not in text_words and w not in missing:
                missing.append(w)
    return missing


def type_from_title(alert: dict, record: dict) -> dict:
    """Where a regulator states the alert class in the title, trust the title over the model.
    MHRA: "Medicines Recall" is a recall; "Medicines Defect Notification" asks for caution, not removal."""
    title = alert.get("title", "")
    if record and alert.get("source", "").startswith("MHRA"):
        if re.search(r"(?i)defect notification", title):
            record["alert_type"] = "defect_notice"
        elif re.search(r"(?i)medicines? recall", title):
            record["alert_type"] = "recall"
    return record


def _attempt(client, model_id: str, alert: dict, meter, max_tokens: int = 12000) -> dict:
    """One model call. Returns the record and grounding checks, or the error."""
    user_msg = f"Source: {alert['source']}\nTitle: {alert['title']}\nDate: {alert['date']}\n\nAlert text:\n{alert['text']}"
    try:
        resp = client.chat.completions.create(
            model=model_id,
            temperature=0,
            max_tokens=max_tokens,  # reasoning plus a long multi-product reply can need well over 8k tokens
            response_format={"type": "json_object"},  # constrained decoding: the reply is always valid JSON
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}],
        )
        meter.add(resp.usage, model_id)
        if resp.choices[0].finish_reason == "length":
            raise ValueError("reply cut off at max_tokens")
        return run_checks(parse_json_reply(resp.choices[0].message.content), alert)
    except Exception as e:  # keep the batch run going; the error is written next to the alert
        return {"record": None, "grounding": [], "missed_candidates": [], "name_words_not_in_source": [],
                "all_batches_missing": False, "misattached_batches": [], "broken_fragments": 0,
                "error": f"{type(e).__name__}: {e}"}


def run_checks(record: dict, alert: dict) -> dict:
    """Clean a reading and run every deterministic check on it: for a fresh reply, or to re-check a stored one."""
    record = type_from_title(alert, normalise_record(record))
    record["all_removed_unsupported"] = drop_unsupported_all(record, alert["text"]) or record.get("all_removed_unsupported", False)
    return {"record": record, "grounding": check_grounding(record, alert["text"]),
            "missed_candidates": missed_candidates(record, alert["text"]),
            "name_words_not_in_source": ungrounded_name_words(record, alert["text"]),
            "all_batches_missing": all_batches_missing(record, alert["text"]),
            "misattached_batches": misattached_batches(record, alert["text"]),
            "broken_fragments": record.get("broken_fragments", 0), "error": None}


def needs_escalation(result: dict) -> str | None:
    """Reason to hand the alert to a stronger model, or None if the result passed every check."""
    if result["error"]:
        return result["error"][:120]
    if result.get("broken_fragments"):
        return f"reply contained {result['broken_fragments']} broken JSON fragments"
    if any(not g["in_source"] for g in result["grounding"]):
        return "batch number not found in the alert text"
    if result["missed_candidates"]:
        return f"possible missed batches: {result['missed_candidates'][:5]}"
    if result["all_batches_missing"]:
        return "alert says all batches/lots are affected but the answer has no ALL entry"
    if result["misattached_batches"]:
        return f"batches filed under the wrong product: {result['misattached_batches'][:5]}"
    if result["name_words_not_in_source"]:
        return f"product name words not in the alert: {result['name_words_not_in_source'][:5]}"
    return None


def _quality(result: dict) -> tuple:
    """Higher is better: no error, everything grounded, nothing missed or misfiled, no name slips, most batches."""
    return (result["error"] is None,
            not result.get("broken_fragments"),
            all(g["in_source"] for g in result["grounding"]),
            -len(result["missed_candidates"]),
            not result["all_batches_missing"],
            -len(result["misattached_batches"]),
            -len(result["name_words_not_in_source"]),
            len(result["grounding"]))


def extract_alert(client, model_id: str, alert: dict, meter, fallback_model_id: str | None = None) -> dict:
    """Extract with the fast model; if the reply fails validation, escalate once to the stronger model
    and keep whichever answer passes more checks.

    Validation: valid and complete JSON, every batch number appears in the source text, and no batch that a
    deterministic reader sees in the text is missing from the answer.
    """
    result = _attempt(client, model_id, alert, meter)
    result["model_used"] = model_id
    reason = needs_escalation(result)
    if reason and fallback_model_id:
        second = _attempt(client, fallback_model_id, alert, meter, max_tokens=24000)
        second["model_used"] = fallback_model_id
        best = second if _quality(second) >= _quality(result) else result
        best["escalated_because"] = reason
        result = best
    fill_from_reader(result, alert["text"])
    return result


def fill_from_reader(result: dict, source_text: str) -> None:
    """Last resort for a single-product alert: add batch numbers the rule-based reader found but both models missed.

    The reader reported no false batch numbers on the 90-alert evaluation set, and with one product there is no
    doubt about where a number belongs. Added batches are marked, and nothing is added to multi-product alerts."""
    record = result.get("record") or {}
    products = record.get("products") or []
    if result.get("error") or len(products) != 1 or not result.get("missed_candidates"):
        return
    for number in result["missed_candidates"]:
        products[0].setdefault("batches", []).append(
            {"batch_number": number, "expiry_date": None, "added_by": "rule-based reader"})
    result["batches_added_by_reader"] = list(result["missed_candidates"])
    result["grounding"] = check_grounding(record, source_text)
    result["missed_candidates"] = []
