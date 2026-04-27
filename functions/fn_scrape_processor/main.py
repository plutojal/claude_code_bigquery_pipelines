"""GCS-triggered Cloud Function — inserts normalized CSV into stores_normalized."""

import csv
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import bigquery, storage

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
TABLE = "stores_normalized"
NORMALIZED_PREFIX = "normalized/"

BATCH_SIZE = 1000
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
            row["ingested_at"] = ingested_at
            row["source_file"] = source_file
            rows.append(row)

    return rows


def insert_rows(rows: list[dict]) -> None:
    client = bigquery.Client(project=PROJECT)
    table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
    total = len(rows)

    for start in range(0, total, BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        errors = client.insert_rows_json(table_ref, batch)
        if errors:
            raise RuntimeError(f"BigQuery insert errors at row {start}: {errors}")
        inserted = min(start + BATCH_SIZE, total)
        print(f"{inserted}/{total} rows inserted")

    print(f"Done — {total} rows inserted into {table_ref}.")


def process_file(bucket_name: str, file_name: str) -> None:
    storage_client = storage.Client(project=PROJECT)
    blob = storage_client.bucket(bucket_name).blob(file_name)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        blob.download_to_filename(tmp_path)
        rows = load_csv(tmp_path, source_file=Path(file_name).name)
        if not rows:
            print(f"No rows found in {file_name} — nothing inserted.")
            return
        insert_rows(rows)
    finally:
        os.unlink(tmp_path)


@functions_framework.cloudEvent
def fn_scrape_processor(cloud_event: CloudEvent) -> None:
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    if not file_name.startswith(NORMALIZED_PREFIX):
        print(f"Skipping {file_name} — not in {NORMALIZED_PREFIX}")
        return

    print(f"Processing gs://{bucket_name}/{file_name}")
    process_file(bucket_name, file_name)
