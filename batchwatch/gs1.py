"""Parse GS1 element strings from the 2D DataMatrix printed on medicine packs.

A pack code carries Application Identifiers (AIs): (01) GTIN, (17) expiry YYMMDD, (10) batch/lot, (21) serial
number, (11) production date. Scanners return them either raw, with the GS character (ASCII 29) closing each
variable-length field, or in the bracketed human-readable form "(01)05012748615328(17)290228(10)487069".
"""
import re
from calendar import monthrange

GS = "\x1d"
FIXED = {"00": 18, "01": 14, "02": 14, "11": 6, "13": 6, "15": 6, "16": 6, "17": 6}
VARIABLE = {"10": 20, "21": 20, "22": 20, "240": 30, "241": 30, "30": 8, "710": 20, "711": 20, "712": 20, "713": 20, "714": 20}
SYMBOLOGY_PREFIX = re.compile(r"^\](?:d2|C1|Q3|e0)")


def _date(yymmdd: str) -> str | None:
    """GS1 dates are YYMMDD; day 00 means the last day of the month."""
    if not re.fullmatch(r"\d{6}", yymmdd or ""):
        return None
    year, month, day = 2000 + int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:])
    if not 1 <= month <= 12:
        return None
    day = day or monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{day:02d}"


def _read_ai(s: str, i: int) -> str | None:
    for size in (2, 3, 4):
        ai = s[i:i + size]
        if ai in FIXED or ai in VARIABLE:
            return ai
    return None


def parse_gs1(raw: str) -> dict:
    """Return {"gtin", "batch", "expiry", "serial", "production"} for whatever the code contains (missing keys absent)."""
    s = SYMBOLOGY_PREFIX.sub("", (raw or "").strip())
    fields: dict[str, str] = {}
    if s.startswith("("):
        for ai, value in re.findall(r"\((\d{2,4})\)([^(]*)", s):
            fields[ai] = value.strip()
    else:
        s = s.lstrip(GS)
        i = 0
        while i < len(s):
            ai = _read_ai(s, i)
            if ai is None:
                break  # not a GS1 element string, or trailing data we do not understand
            i += len(ai)
            if ai in FIXED:
                fields[ai] = s[i:i + FIXED[ai]]
                i += FIXED[ai]
            else:
                end = s.find(GS, i)
                end = len(s) if end == -1 else end
                fields[ai] = s[i:min(end, i + VARIABLE[ai])]
                i = end
            if i < len(s) and s[i] == GS:
                i += 1
    out = {}
    if fields.get("01"):
        out["gtin"] = fields["01"]
    if fields.get("10"):
        out["batch"] = fields["10"]
    if fields.get("17") and _date(fields["17"]):
        out["expiry"] = _date(fields["17"])
    if fields.get("21"):
        out["serial"] = fields["21"]
    if fields.get("11") and _date(fields["11"]):
        out["production"] = _date(fields["11"])
    return out
