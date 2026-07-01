"""Geocode stores missing lat/lng via the Google Maps Geocoding API.

Pulls stores from mart_stores where latitude IS NULL, geocodes their
address, and MERGEs results into the store_geocodes lookup table.
The zip code from the geocode result is captured too — missing zips are
the main reason coordinates are blank (mart_stores falls back to
zip_lookup centroids), so filling zip also unlocks pop density, area
type, and county. mart_stores joins against store_geocodes to coalesce
the blanks.

Runs are cheap: stores already present in store_geocodes are skipped,
so the API is only called for addresses that have never been resolved.

Requires:
    GOOGLE_MAPS_API_KEY env var — injected from Secret Manager at deploy
    time (see deploy_fn_store_geocoder.sh).
"""

import os
import time
from datetime import datetime, timezone

import functions_framework
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

def _extract_zip(result: dict) -> str | None:
    """Pulls the 5-digit postal_code from the geocode result's address components."""
    for component in result.get("address_components", []):
        if "postal_code" in component.get("types", []):
            return component.get("short_name", "")[:5] or None
    return None


def _geocode_address(address: str, api_key: str) -> dict | None:
    """Returns {lat, lng, zip, formatted_address, location_type} or None if unresolvable."""
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
                "zip": _extract_zip(result),
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

def _fetch_stores(client: bigquery.Client, limit: int | None = None) -> list:
    """Stores missing coordinates that have not already been geocoded.

    geocode_query concatenates address, city, state, and zip (whatever is
    present) so Google gets full context even when the raw address field
    is street-only.  limit caps the number of stores for test runs.
    """
    sql = f"""
        SELECT
            s.store_id,
            ARRAY_TO_STRING([s.address, s.parsed_city, s.state, s.zip], ', ')
                AS geocode_query
        FROM `{PROJECT}.{DATASET}.{SOURCE_TABLE}` AS s
        LEFT JOIN `{PROJECT}.{DATASET}.{GEOCODE_TABLE}` AS g
          ON g.store_id = s.store_id
        WHERE s.latitude IS NULL
          AND s.address IS NOT NULL
          AND g.store_id IS NULL
        ORDER BY s.store_id
    """
    if limit:
        sql += f"        LIMIT {int(limit)}\n"
    return list(client.query(sql).result())


def _apply_updates(client: bigquery.Client, rows: list[dict]) -> None:
    if not rows:
        return

    staging = f"{PROJECT}.{DATASET}._store_geocodes_staging"
    schema = [
        bigquery.SchemaField("store_id",          "STRING"),
        bigquery.SchemaField("lat",               "FLOAT64"),
        bigquery.SchemaField("lng",               "FLOAT64"),
        bigquery.SchemaField("zip",               "STRING"),
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
            t.zip = s.zip,
            t.formatted_address = s.formatted_address,
            t.location_type = s.location_type,
            t.geocoded_at = s.geocoded_at
        WHEN NOT MATCHED BY TARGET THEN INSERT ROW
    """
    client.query(sql).result()
    client.delete_table(staging)
    print(f"  Wrote {len(rows)} rows to {GEOCODE_TABLE}.")


# ---------------------------------------------------------------------------
# Main geocoding run
# ---------------------------------------------------------------------------

def _run_geocoding(client: bigquery.Client, api_key: str, limit: int | None = None) -> str:
    stores = _fetch_stores(client, limit)
    total = len(stores)
    if limit:
        print(f"Test mode: limited to {limit} store(s).")
    if total == 0:
        print("All stores already have coordinates or geocode entries.")
        return "0 geocoded, 0 unresolved"
    print(f"{total} stores need geocoding.")

    now = datetime.now(timezone.utc).isoformat()
    resolved: list[dict] = []
    failed = 0
    for i, store in enumerate(stores, start=1):
        hit = _geocode_address(store.geocode_query, api_key)
        if hit:
            resolved.append({"store_id": store.store_id, "geocoded_at": now, **hit})
        else:
            failed += 1
            print(f"  [{i}/{total}] no result: {store.store_id} — {store.geocode_query}")
        if i % 25 == 0 or i == total:
            print(f"[{i}/{total}] geocoded, {len(resolved)} resolved, {failed} failed.")
        time.sleep(SLEEP_SECS)

    print(f"Applying {len(resolved)}/{total} updates ...")
    _apply_updates(client, resolved)
    summary = f"{len(resolved)} geocoded, {failed} unresolved"
    print(f"Done — {summary}")
    return summary


# ---------------------------------------------------------------------------
# Entry point — HTTP trigger (called by Cloud Scheduler)
# ---------------------------------------------------------------------------

@functions_framework.http
def fn_store_geocoder(request):
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return "GOOGLE_MAPS_API_KEY is not configured on this function.", 500

    # Optional ?limit=N for test runs — caps how many stores get geocoded.
    limit = None
    raw_limit = request.args.get("limit")
    if raw_limit:
        try:
            limit = max(1, int(raw_limit))
        except ValueError:
            return f"Invalid limit '{raw_limit}'. Use a positive integer.", 400

    client = bigquery.Client(project=PROJECT)
    summary = _run_geocoding(client, api_key, limit)
    return f"OK ({summary})", 200
