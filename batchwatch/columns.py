"""Read an unfamiliar stock export: ask Nemotron which column holds the product, the batch, the expiry ...

Pharmacy and hospital systems name their columns in many ways ("Desc", "Lot/Serial", "رقم التشغيلة", "Col 7").
Known names are matched without any model (batchwatch.match.map_columns). Only when that fails is Nemotron 3 Nano
shown the header row and three sample rows, and asked for a mapping; every header it names must exist in the file,
and the product and batch columns are still required, so a wrong guess fails loudly instead of matching quietly.
"""
import json

from batchwatch.extract import parse_json_reply
from batchwatch.llm import MODELS

FIELDS = {
    "product_name": "trade or generic name of the medicine",
    "batch_number": "batch or lot number printed on the pack",
    "strength": "strength or dose, e.g. 500 mg",
    "manufacturer": "manufacturer or marketing authorisation holder",
    "expiry": "expiry date",
    "gtin": "barcode / GTIN / EAN number",
    "quantity": "quantity in stock",
    "location": "store, shelf, ward or branch",
    "item_id": "the system's own item code",
}

SYSTEM = """You map the columns of a pharmacy or hospital stock export to known fields.
Fields: """ + "; ".join(f"{k} = {v}" for k, v in FIELDS.items()) + """.
Use only header names that appear exactly in the list given. Leave a field out if no column fits.
Reply with JSON only: {"mapping": {"<field>": "<header exactly as given>"}}"""


def suggest_columns(client, headers: list[str], rows: list[dict], meter=None, model_key: str = "nano") -> dict:
    sample = [{h: str(r.get(h, ""))[:40] for h in headers} for r in rows[:3]]
    model_id = MODELS[model_key][0]
    resp = client.chat.completions.create(
        model=model_id, temperature=0, max_tokens=4000, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": json.dumps({"headers": headers, "sample_rows": sample}, ensure_ascii=False)}])
    if meter is not None:
        meter.add(resp.usage, model_id)
    data = parse_json_reply(resp.choices[0].message.content or "{}")
    mapping, used = {}, set()
    for field_name, header in (data.get("mapping") or {}).items():
        if field_name in FIELDS and header in headers and header not in used:
            mapping[field_name] = header
            used.add(header)
    return mapping if plausible(mapping, rows) else {}


def plausible(mapping: dict, rows: list[dict]) -> bool:
    """The values must look the part: batch numbers carry a digit, product names carry letters."""
    values = lambda f: [str(r.get(mapping[f]) or "").strip() for r in rows[:20] if str(r.get(mapping[f]) or "").strip()]  # noqa: E731
    if "batch_number" not in mapping or "product_name" not in mapping:
        return False
    batches, names = values("batch_number"), values("product_name")
    return (bool(batches) and sum(any(ch.isdigit() for ch in b) for b in batches) >= len(batches) / 2
            and bool(names) and sum(any(ch.isalpha() for ch in n) for n in names) >= len(names) / 2)
