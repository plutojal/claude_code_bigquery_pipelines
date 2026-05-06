"""Backfill missing zip codes (and lat/lng) in stores_normalized.

Uses the US Census Geocoder batch API — free, no API key required.
Submits up to 9,500 addresses per batch, extracts zip + coordinates
from the matched address, and writes them back via MERGE.

Usage:
    python backfill_zips.py
"""

import csv
import io
import re
import time

import requests
from google.cloud import bigquery

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
TABLE = "stores_normalized"

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BATCH_SIZE = 9_500   # Census hard limit is 10 000; stay under
SLEEP_SECS = 2       # polite pause between batches

_ZIP_RE = re.compile(r"\b(\d{5})\b")


# ---------------------------------------------------------------------------
# Census Geocoder
# ---------------------------------------------------------------------------

def _build_csv(rows: list) -> str:
    lines = ["ID,Street,City,State,ZIP"]
    for row in rows:
        street = re.sub(r",", " ", row.address or "").strip()
        city   = re.sub(r",", " ", row.parsed_city or "").strip()
        state  = (row.state or "").strip()
        lines.append(f"{row.store_id},{street},{city},{state},")
    return "\n".join(lines)


def _geocode_batch(rows: list) -> dict[str, tuple[str, float | None, float | None]]:
    """Returns {store_id: (zip, lat, lng)} for matched addresses."""
    payload = _build_csv(rows)
    resp = requests.post(
        CENSUS_URL,
        files={"addressFile": ("addr.csv", payload.encode(), "text/csv")},
        data={"benchmark": "Public_AR_Current"},
        timeout=300,
    )
    resp.raise_for_status()

    results: dict[str, tuple[str, float | None, float | None]] = {}
    reader = csv.reader(io.StringIO(resp.text))
    for line in reader:
        if len(line) < 6:
            continue
        store_id   = line[0].strip()
        match_flag = line[2].strip().upper()
        if match_flag not in ("MATCH", "TIED"):
            continue
        matched_addr = line[4].strip()   # e.g. "60 MAIN ST, MATAWAN, NJ, 07747"
        coords       = line[5].strip()   # e.g. "-74.123,40.456"  (lon,lat)

        # Extract zip — last comma-separated segment that is 5 digits
        zip_code: str | None = None
        for segment in reversed(matched_addr.split(",")):
            m = _ZIP_RE.search(segment.strip())
            if m:
                zip_code = m.group(1)
                break

        lat = lng = None
        if coords and "," in coords:
            try:
                lon_s, lat_s = coords.split(",", 1)
                lng, lat = float(lon_s), float(lat_s)
            except ValueError:
                pass

        if zip_code:
            results[store_id] = (zip_code, lat, lng)

    return results


# ---------------------------------------------------------------------------
# BigQuery helpers
# ---------------------------------------------------------------------------

def _fetch_stores(client: bigquery.Client) -> list:
    sql = f"""
        SELECT store_id, address, parsed_city, state
        FROM `{PROJECT}.{DATASET}.{TABLE}`
        WHERE zip IS NULL AND address IS NOT NULL
        ORDER BY store_id
    """
    return list(client.query(sql).result())


def _apply_updates(
    client: bigquery.Client,
    updates: dict[str, tuple[str, float | None, float | None]],
) -> None:
    if not updates:
        return

    rows = [
        {"store_id": sid, "zip": z, "lat": lat, "lng": lng}
        for sid, (z, lat, lng) in updates.items()
    ]

    staging = f"{PROJECT}.{DATASET}._zip_backfill_staging"
    schema = [
        bigquery.SchemaField("store_id", "STRING"),
        bigquery.SchemaField("zip",      "STRING"),
        bigquery.SchemaField("lat",      "FLOAT64"),
        bigquery.SchemaField("lng",      "FLOAT64"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_json(rows, staging, job_config=job_config).result()

    sql = f"""
        MERGE `{PROJECT}.{DATASET}.{TABLE}` AS t
        USING `{staging}` AS s
        ON t.store_id = s.store_id
        WHEN MATCHED THEN UPDATE SET
            t.zip = s.zip,
            t.lat = IF(t.lat IS NULL, s.lat, t.lat),
            t.lng = IF(t.lng IS NULL, s.lng, t.lng)
    """
    client.query(sql).result()
    client.delete_table(staging)
    print(f"  Wrote {len(rows)} rows to {TABLE}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    client = bigquery.Client(project=PROJECT)

    stores = _fetch_stores(client)
    total = len(stores)
    if total == 0:
        print("All stores already have zip codes.")
        return
    print(f"{total} stores need zip backfill.")

    all_updates: dict[str, tuple[str, float | None, float | None]] = {}
    for start in range(0, total, BATCH_SIZE):
        batch = stores[start : start + BATCH_SIZE]
        end = start + len(batch)
        print(f"Geocoding [{start+1}–{end}] ...")
        updates = _geocode_batch(batch)
        all_updates.update(updates)
        print(f"  {len(updates)}/{len(batch)} matched.")
        if end < total:
            time.sleep(SLEEP_SECS)

    print(f"\nApplying {len(all_updates)}/{total} updates ...")
    _apply_updates(client, all_updates)
    print(f"Done — {len(all_updates)} zip codes backfilled.")


if __name__ == "__main__":
    main()
