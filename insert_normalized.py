"""Stream a scrape CSV into BigQuery table stores_normalized (upsert on store_id)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

# Shared parsing + store_id logic lives in the Cloud Function library.
sys.path.insert(0, str(Path(__file__).parent / "functions" / "fn_retail_store_scrape_processor"))
from _lib import load_csv  # noqa: E402

from google.cloud import bigquery  # noqa: E402

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
TABLE = "stores_normalized"
BATCH_SIZE = 1000


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


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <path/to/scrape.csv>")
        sys.exit(1)

    path = sys.argv[1]
    rows = load_csv(path, source_file=Path(path).name)
    if not rows:
        print("No rows found — nothing inserted.")
        return

    insert_rows(rows)


if __name__ == "__main__":
    main()
