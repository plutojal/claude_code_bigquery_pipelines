"""Geocode stores missing lat/lng via the Google Maps Geocoding API.

Pulls stores from mart_stores where latitude IS NULL, geocodes their
address, and streams each result into the store_geocodes lookup table
row by row — an interrupted run keeps everything geocoded so far and
resumes at the first unwritten store.
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


def _insert_row(client: bigquery.Client, row: dict) -> bool:
    """Streams a single geocode result into store_geocodes immediately.

    Per-row writes mean a crashed or timed-out run loses nothing — every
    geocode already paid for is in the table, and the next run resumes
    at the first unwritten store (the fetch query skips existing rows).
    """
    errors = client.insert_rows_json(f"{PROJECT}.{DATASET}.{GEOCODE_TABLE}", [row])
    if errors:
        print(f"  insert failed for {row['store_id']}: {errors}")
        return False
    return True


# ---------------------------------------------------------------------------
# Main geocoding run
# ---------------------------------------------------------------------------

def _count_pending(client: bigquery.Client) -> int:
    """Total stores still missing coordinates and not yet geocoded."""
    sql = f"""
        SELECT COUNT(*) AS cnt
        FROM `{PROJECT}.{DATASET}.{SOURCE_TABLE}` AS s
        LEFT JOIN `{PROJECT}.{DATASET}.{GEOCODE_TABLE}` AS g
          ON g.store_id = s.store_id
        WHERE s.latitude IS NULL
          AND s.address IS NOT NULL
          AND g.store_id IS NULL
    """
    return next(client.query(sql).result()).cnt


def _run_geocoding(client: bigquery.Client, api_key: str, limit: int | None = None) -> str:
    pending = _count_pending(client)
    if pending == 0:
        print("All stores already have coordinates or geocode entries.")
        return "0 pending, 0 processed, 0 geocoded, 0 unresolved, 0 remaining"

    stores = _fetch_stores(client, limit)
    total = len(stores)
    if limit:
        print(f"Test mode: processing {total} of {pending} pending store(s).")
    else:
        print(f"{pending} stores need geocoding.")

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    failed = 0
    for i, store in enumerate(stores, start=1):
        hit = _geocode_address(store.geocode_query, api_key)
        if hit and _insert_row(client, {"store_id": store.store_id, "geocoded_at": now, **hit}):
            written += 1
            print(
                f"[{i}/{total}] {store.store_id} → "
                f"({hit['lat']:.5f}, {hit['lng']:.5f}) zip={hit['zip']} {hit['location_type']}"
            )
        else:
            failed += 1
            if not hit:
                print(f"[{i}/{total}] {store.store_id} → no result: {store.geocode_query}")
        time.sleep(SLEEP_SECS)

    remaining = pending - written
    summary = (
        f"{pending} pending, {total} processed, {written} geocoded, "
        f"{failed} unresolved, {remaining} remaining"
    )
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
