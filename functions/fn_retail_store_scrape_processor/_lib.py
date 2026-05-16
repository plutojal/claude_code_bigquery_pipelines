"""Shared CSV parsing + store_id logic — imported by both the Cloud Function and insert_normalized.py."""

import csv
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

BOOL_TRUE = {"true", "1", "yes", "y"}
BOOL_FALSE = {"false", "0", "no", "n", ""}

STRING_COLUMNS = [
    "brand", "store_name", "address", "phone", "email",
    "parsed_country", "parsed_city", "house_number", "road",
    "zip", "state", "chain_name",
]

_ABBREV_ID = {
    "st": "street", "ave": "avenue", "blvd": "boulevard", "dr": "drive",
    "rd": "road", "ln": "lane", "ct": "court", "pl": "place",
    "hwy": "highway", "pkwy": "parkway", "rt": "route", "rte": "route",
    "ste": "suite", "apt": "apartment",
    "n": "north", "s": "south", "e": "east", "w": "west",
}


def _norm_id_field(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[^\w\s]", " ", text.lower().strip())
    return " ".join(_ABBREV_ID.get(t, t) for t in text.split())


def make_store_id(name: str, house_number: str, road: str, city: str, state: str) -> str:
    key = f"{_norm_id_field(name)}|{_norm_id_field(house_number)}|{_norm_id_field(road)}|{_norm_id_field(city)}|{_norm_id_field(state)}"
    return hashlib.md5(key.encode()).hexdigest()


def parse_bool(value: str) -> bool | None:
    v = value.strip().lower()
    if v in BOOL_TRUE:
        return True
    if v in BOOL_FALSE:
        return False
    return None


def _parse_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def _parse_int(value: str) -> int | None:
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return None


def load_csv(path: str, source_file: str) -> list[dict]:
    rows = []
    ingested_at = datetime.now(timezone.utc).isoformat()

    with open(path, newline="", encoding="utf-8") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        reader = csv.DictReader(fh, dialect=dialect)

        for raw in reader:
            row: dict = {col: raw.get(col, "").strip() or None for col in STRING_COLUMNS}
            row["is_chain"] = parse_bool(raw.get("is_chain", ""))
            row["rating"] = _parse_float(raw.get("rating", ""))
            row["review_count"] = _parse_int(raw.get("review_count", ""))
            row["ingested_at"] = ingested_at
            row["source_file"] = source_file
            row["store_id"] = make_store_id(
                raw.get("store_name", ""),
                raw.get("house_number", ""),
                raw.get("road", ""),
                raw.get("parsed_city", ""),
                raw.get("state", ""),
            )
            rows.append(row)

    return rows
