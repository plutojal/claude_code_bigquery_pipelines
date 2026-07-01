"""Geocode stores missing lat/lng via the Google Maps Geocoding API.

Pulls stores from mart_stores where latitude IS NULL, geocodes their
address, and MERGEs results into the store_geocodes lookup table.
mart_store_locator joins against store_geocodes to coalesce the blanks.

Reruns are cheap: stores already present in store_geocodes are skipped,
so the API is only called for addresses that have never been resolved.

Requires:
    GOOGLE_MAPS_API_KEY env var (Geocoding API enabled on the key)

Usage:
    python geocode_missing_coords.py
"""

import os
import sys
import time
from datetime import datetime, timezone

import requests
from google.cloud import bigquery

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
SOURCE_TABLE = "mart_stores"
GEOCODE_TABLE = "store_geocodes"

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
SLEEP_SECS = 0.05        # ~20 QPS, well under the default 50 QPS quota
MAX_RETRIES = 3          # per address, on OVER_QUERY_LIMIT / transient errors


# ---------------------------------------------------------------------------
# Google Maps Geocoding API
# ---------------------------------------------------------------------------

def _geocode_address(address: str, api_key: str) -> dict | None:
    """Returns {lat, lng, formatted_address, location_type} or None if unresolvable."""
    params = {
        "address": address,
        "components": "country:US",
        "key": api_key,
    }
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(GEOCODE_URL, params=params, timeout=30)
            resp.raise_for_status()
            body = resp.json()
        except requests.RequestException as exc:
            print(f"  Request error ({exc}); retrying...")
            time.sleep(2 ** attempt)
            continue

        status = body.get("status")
        if status == "OK":
            result = body["results"][0]
            location = result["geometry"]["location"]
            return {
                "lat": location["lat"],
                "lng": location["lng"],
                "formatted_address": result.get("formatted_address"),
                "location_type": result["geometry"].get("location_type"),
            }
        if status == "ZERO_RESULTS":
            return None
        if status == "OVER_QUERY_LIMIT":
            print("  Rate limited; backing off...")
            time.sleep(2 ** (attempt + 1))
            continue
        # REQUEST_DENIED / INVALID_REQUEST / UNKNOWN_ERROR
        print(f"  Geocode failed for '{address}': {status} — {body.get('error_message', '')}")
        return None
    return None


# ---------------------------------------------------------------------------
# BigQuery helpers
# ---------------------------------------------------------------------------

def _fetch_stores(client: bigquery.Client) -> list:
    """Stores missing coordinates that have not already been geocoded."""
    sql = f"""
        SELECT s.store_id, s.address
        FROM `{PROJECT}.{DATASET}.{SOURCE_TABLE}` AS s
        LEFT JOIN `{PROJECT}.{DATASET}.{GEOCODE_TABLE}` AS g
          ON g.store_id = s.store_id
        WHERE s.latitude IS NULL
          AND s.address IS NOT NULL
          AND g.store_id IS NULL
        ORDER BY s.store_id
    """
    return list(client.query(sql).result())


def _apply_updates(client: bigquery.Client, rows: list[dict]) -> None:
    if not rows:
        return

    staging = f"{PROJECT}.{DATASET}._store_geocodes_staging"
    schema = [
        bigquery.SchemaField("store_id",          "STRING"),
        bigquery.SchemaField("lat",               "FLOAT64"),
        bigquery.SchemaField("lng",               "FLOAT64"),
        bigquery.SchemaField("formatted_address", "STRING"),
        bigquery.SchemaField("location_type",     "STRING"),
        bigquery.SchemaField("geocoded_at",       "TIMESTAMP"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_json(rows, staging, job_config=job_config).result()

    sql = f"""
        MERGE `{PROJECT}.{DATASET}.{GEOCODE_TABLE}` AS t
        USING `{staging}` AS s
        ON t.store_id = s.store_id
        WHEN MATCHED THEN UPDATE SET
            t.lat = s.lat,
            t.lng = s.lng,
            t.formatted_address = s.formatted_address,
            t.location_type = s.location_type,
            t.geocoded_at = s.geocoded_at
        WHEN NOT MATCHED BY TARGET THEN INSERT ROW
    """
    client.query(sql).result()
    client.delete_table(staging)
    print(f"  Wrote {len(rows)} rows to {GEOCODE_TABLE}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        print("Error: GOOGLE_MAPS_API_KEY env var is not set.")
        sys.exit(1)

    client = bigquery.Client(project=PROJECT)

    stores = _fetch_stores(client)
    total = len(stores)
    if total == 0:
        print("All stores already have coordinates or geocode entries.")
        return
    print(f"{total} stores need geocoding.")

    now = datetime.now(timezone.utc).isoformat()
    resolved: list[dict] = []
    failed = 0
    for i, store in enumerate(stores, start=1):
        hit = _geocode_address(store.address, api_key)
        if hit:
            resolved.append({"store_id": store.store_id, "geocoded_at": now, **hit})
        else:
            failed += 1
            print(f"  [{i}/{total}] no result: {store.store_id} — {store.address}")
        if i % 25 == 0 or i == total:
            print(f"[{i}/{total}] geocoded, {len(resolved)} resolved, {failed} failed.")
        time.sleep(SLEEP_SECS)

    print(f"\nApplying {len(resolved)}/{total} updates ...")
    _apply_updates(client, resolved)
    print(f"Done — {len(resolved)} geocoded, {failed} unresolved.")


if __name__ == "__main__":
    main()
