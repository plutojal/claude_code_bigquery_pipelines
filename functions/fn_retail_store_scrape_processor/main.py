"""GCS-triggered Cloud Function — inserts normalized CSV into stores_normalized."""

import csv
import hashlib
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


def _make_store_id(name: str, address: str, city: str, state: str) -> str:
    key = f"{name}|{address}|{city}|{state}".lower().strip()
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
            row["store_id"] = _make_store_id(
                raw.get("store_name", ""),
                raw.get("address", ""),
                raw.get("parsed_city", ""),
                raw.get("state", ""),
            )
            rows.append(row)

    return rows


def insert_rows(rows: list[dict]) -> None:
    client = bigquery.Client(project=PROJECT)
    table_ref = f"{PROJECT}.{DATASET}.{TABLE}"
    schema = client.get_table(table_ref).schema
    total = len(rows)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    staging_ref = f"{PROJECT}.{DATASET}.{TABLE}_staging_{run_ts}"

    for start in range(0, total, BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        disposition = (
            bigquery.WriteDisposition.WRITE_TRUNCATE if start == 0
            else bigquery.WriteDisposition.WRITE_APPEND
        )
        job_config = bigquery.LoadJobConfig(schema=schema, write_disposition=disposition)
        client.load_table_from_json(batch, staging_ref, job_config=job_config).result()
        print(f"{min(start + BATCH_SIZE, total)}/{total} rows staged")

    sql = f"""
        MERGE `{table_ref}` AS t
        USING `{staging_ref}` AS s
        ON t.store_id = s.store_id
        WHEN MATCHED THEN UPDATE SET
            t.brand = s.brand,
            t.store_name = s.store_name,
            t.address = s.address,
            t.phone = s.phone,
            t.email = s.email,
            t.parsed_country = s.parsed_country,
            t.parsed_city = s.parsed_city,
            t.house_number = s.house_number,
            t.road = s.road,
            t.zip = s.zip,
            t.state = s.state,
            t.is_chain = s.is_chain,
            t.chain_name = s.chain_name,
            t.rating = s.rating,
            t.review_count = s.review_count,
            t.ingested_at = s.ingested_at,
            t.source_file = s.source_file
        WHEN NOT MATCHED BY TARGET THEN INSERT ROW
    """
    client.query(sql).result()
    client.delete_table(staging_ref)
    print(f"Done — {total} rows upserted into {table_ref}.")


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


@functions_framework.cloud_event
def fn_retail_store_scrape_processor(cloud_event: CloudEvent) -> None:
    data = cloud_event.data
    bucket_name = data["bucket"]
    file_name = data["name"]

    if not file_name.startswith(NORMALIZED_PREFIX):
        print(f"Skipping {file_name} — not in {NORMALIZED_PREFIX}")
        return

    print(f"Processing gs://{bucket_name}/{file_name}")
    process_file(bucket_name, file_name)
