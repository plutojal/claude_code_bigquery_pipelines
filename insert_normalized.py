"""Stream a scrape CSV into BigQuery table stores_normalized."""

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
TABLE = "stores_normalized"

BOOL_TRUE = {"true", "1", "yes", "y"}
BOOL_FALSE = {"false", "0", "no", "n", ""}

STRING_COLUMNS = [
    "brand", "store_name", "address", "phone", "email",
    "parsed_country", "parsed_city", "house_number", "road",
    "zip", "state", "chain_name",
]


def parse_bool(value: str) -> bool | None:
    v = value.strip().lower()
    if v in BOOL_TRUE:
        return True
    if v in BOOL_FALSE:
        return False
    return None


def load_csv(path: str) -> list[dict]:
    rows = []
    ingested_at = datetime.now(timezone.utc).isoformat()
    source_file = Path(path).name

    with open(path, newline="", encoding="utf-8") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
        reader = csv.DictReader(fh, dialect=dialect)

        for raw in reader:
            row: dict = {col: raw.get(col, "").strip() or None for col in STRING_COLUMNS}
            row["is_chain"] = parse_bool(raw.get("is_chain", ""))
            row["ingested_at"] = ingested_at
            row["source_file"] = source_file
            rows.append(row)

    return rows


def insert_rows(rows: list[dict]) -> None:
    client = bigquery.Client(project=PROJECT)
    table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
    errors = client.insert_rows_json(table_ref, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")
    print(f"Inserted {len(rows)} rows into {table_ref}.")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <path/to/scrape.csv>")
        sys.exit(1)

    rows = load_csv(sys.argv[1])
    if not rows:
        print("No rows found — nothing inserted.")
        return

    insert_rows(rows)


if __name__ == "__main__":
    main()
