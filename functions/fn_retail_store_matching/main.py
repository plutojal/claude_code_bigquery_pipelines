"""Daily matching function — enriches stores_normalized with Salesforce and distributor matches."""

import string
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import functions_framework
from google.cloud import bigquery
from rapidfuzz import fuzz

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
UNIFIED_TABLE = "unified_businesses"
BATCH_SIZE = 1000

# API layers (4 & 5) only run for these states — controls cost.
TARGET_STATES = {
    "CA", "TX", "FL", "NY", "IL",
    "WA", "PA", "OH", "GA", "NC",
}

FUZZY_AUTO = 85
FUZZY_FLAG = 65

ABBREV = {
    "st": "street", "ave": "avenue", "blvd": "boulevard", "dr": "drive",
    "rd": "road", "ln": "lane", "ct": "court", "pl": "place",
    "hwy": "highway", "pkwy": "parkway",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "ste": "suite", "apt": "apartment",
}

# ---------------------------------------------------------------------------
# Source configs
# ---------------------------------------------------------------------------

SALESFORCE_CONFIG = {
    "table": f"{PROJECT}.{DATASET}.v_salesforce_accounts",
    "id_col": "Id",
    "name_col": "Name",
    "address_col": "BillingStreet",
    "city_col": "BillingCity",
    "state_col": "BillingState",
    "zip_col": "BillingPostalCode",
}

# Add new distributors here — no other code changes needed.
DISTRIBUTORS = [
    {
        "name": "sarene",
        "table": f"{PROJECT}.{DATASET}.v_sarene_comparison_daily",
        # Sarene has no separate city/state/zip — address_full is the full address.
        "id_col": "CONCAT(COALESCE(customer_name,''), '|', COALESCE(address_full,''))",
        "name_col": "customer_name",
        "address_col": "address_full",
        "city_col": "''",
        "state_col": "''",
        "zip_col": "''",
    },
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Record:
    source_id: str
    name: str
    address: str
    city: str
    state: str
    zip_code: str
    norm: str = field(init=False)

    def __post_init__(self):
        self.norm = _normalize(f"{self.address} {self.city} {self.state}")


@dataclass
class DistributorMatch:
    distributor_name: str
    matched: bool
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    match_layer: Optional[str] = None
    match_confidence: Optional[float] = None
    match_status: Optional[str] = None


# ---------------------------------------------------------------------------
# Layer 1 — normalisation
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip().translate(str.maketrans("", "", string.punctuation))
    return " ".join(ABBREV.get(t, t) for t in text.split())


# ---------------------------------------------------------------------------
# Layers 3–5 — fuzzy and API matching
# ---------------------------------------------------------------------------

def _fuzzy(a: Record, b: Record) -> tuple[float, str]:
    score = fuzz.token_sort_ratio(a.norm, b.norm)
    if score >= FUZZY_AUTO:
        return score, "confirmed"
    if score >= FUZZY_FLAG:
        return score, "flagged"
    return score, "no_match"


def _geocode_match(a: Record, b: Record) -> bool:
    # TODO: enable after Google Address Validation API access is granted.
    return False


def _places_lookup(r: Record) -> tuple[Optional[str], Optional[float], Optional[float]]:
    # TODO: enable after Google Maps Places API access is granted.
    return None, None, None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_records(client: bigquery.Client, cfg: dict) -> list[Record]:
    state_col = cfg["state_col"]
    where = f"WHERE {state_col} IS NOT NULL" if not state_col.startswith("'") else ""
    query = f"""
        SELECT
            CAST({cfg['id_col']}  AS STRING) AS source_id,
            {cfg['name_col']}               AS name,
            {cfg['address_col']}            AS address,
            {cfg['city_col']}               AS city,
            {state_col}                     AS state,
            {cfg['zip_col']}                AS zip_code
        FROM `{cfg['table']}`
        {where}
    """
    records = []
    for row in client.query(query).result():
        records.append(Record(
            source_id=row.source_id or "",
            name=row.name or "",
            address=row.address or "",
            city=row.city or "",
            state=row.state or "",
            zip_code=row.zip_code or "",
        ))
    return records


def _load_stores(client: bigquery.Client) -> list[dict]:
    query = f"""
        SELECT
            store_id, brand, store_name, address, phone, email,
            parsed_country, parsed_city, house_number, road,
            zip, state, is_chain, chain_name
        FROM `{PROJECT}.{DATASET}.stores_normalized`
    """
    return [dict(row) for row in client.query(query).result()]


# ---------------------------------------------------------------------------
# Match a single candidate record against a pool
# ---------------------------------------------------------------------------

def _find_match(
    candidate: Record,
    exact_index: dict[str, Record],
    state_bucket: list[Record],
    run_api: bool,
) -> tuple[Optional[Record], str, Optional[float], str]:
    """Returns (record, layer, confidence, status)."""

    # Layer 2: exact
    hit = exact_index.get(candidate.norm)
    if hit:
        return hit, "exact", 100.0, "confirmed"

    # Layer 3: fuzzy
    best_rec, best_score, best_status = None, 0.0, "no_match"
    for rec in state_bucket:
        score, status = _fuzzy(candidate, rec)
        if status != "no_match" and score > best_score:
            best_rec, best_score, best_status = rec, score, status
            if status == "confirmed":
                break
    if best_rec:
        return best_rec, "fuzzy", best_score, best_status

    if not run_api:
        return None, "none", None, "unmatched"

    # Layer 4: geocoding API (stubbed)
    for rec in state_bucket:
        if _geocode_match(candidate, rec):
            return rec, "geocoding", None, "confirmed"

    # Layer 5: places API (stubbed)
    place_id, _, _ = _places_lookup(candidate)
    if place_id:
        return None, "places", None, "confirmed"

    return None, "none", None, "unmatched"


# ---------------------------------------------------------------------------
# Main matching loop
# ---------------------------------------------------------------------------

def _build_index(records: list[Record]) -> tuple[dict[str, Record], dict[str, list[Record]]]:
    exact = {r.norm: r for r in records if r.norm}
    by_state: dict[str, list[Record]] = {}
    for r in records:
        by_state.setdefault(r.state.upper(), []).append(r)
    return exact, by_state


def _store_to_candidate(store: dict) -> Record:
    return Record(
        source_id=store["store_id"],
        name=store.get("store_name") or "",
        address=store.get("address") or "",
        city=store.get("parsed_city") or "",
        state=store.get("state") or "",
        zip_code=store.get("zip") or "",
    )


def _run_matching(client: bigquery.Client) -> None:
    t_start = time.time()
    table_ref = f"{PROJECT}.{DATASET}.{UNIFIED_TABLE}"

    print("Loading stores_normalized...")
    stores = _load_stores(client)
    print(f"Loaded {len(stores)} stores.")

    print("Loading Salesforce...")
    sf_records = _load_records(client, SALESFORCE_CONFIG)
    sf_exact, sf_by_state = _build_index(sf_records)
    print(f"Loaded {len(sf_records)} Salesforce records.")

    dist_indexes = []
    for dist_cfg in DISTRIBUTORS:
        print(f"Loading distributor: {dist_cfg['name']}...")
        records = _load_records(client, dist_cfg)
        exact, by_state = _build_index(records)
        dist_indexes.append((dist_cfg["name"], records, exact, by_state))
        print(f"Loaded {len(records)} records from {dist_cfg['name']}.")

    print(f"All sources loaded in {time.time() - t_start:.1f}s. Truncating {table_ref}...")
    client.query(f"TRUNCATE TABLE `{table_ref}`").result()

    total = len(stores)
    written = 0
    sf_matched_count = 0
    batch: list[dict] = []

    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()

    print(f"Matching {total} stores...")

    for store in stores:
        candidate = _store_to_candidate(store)
        state = candidate.state.upper()
        run_api = state in TARGET_STATES

        # Salesforce match
        sf_rec, sf_layer, sf_conf, sf_status = _find_match(
            candidate, sf_exact, sf_by_state.get(state, []), run_api
        )
        sf_matched = sf_status in ("confirmed", "flagged")
        if sf_matched:
            sf_matched_count += 1

        # Distributor matches
        dist_matches = []
        for dist_name, dist_records, dist_exact, dist_by_state in dist_indexes:
            # Distributors without state go into "" bucket — check both
            bucket = dist_by_state.get(state, []) + dist_by_state.get("", [])
            rec, layer, conf, status = _find_match(candidate, dist_exact, bucket, run_api)
            matched = status in ("confirmed", "flagged")
            dist_matches.append({
                "distributor_name": dist_name,
                "matched": matched,
                "customer_id": rec.source_id if rec else None,
                "customer_name": rec.name if rec else None,
                "match_layer": layer if matched else None,
                "match_confidence": conf if matched else None,
                "match_status": status if matched else None,
            })

        on_any_distributor = any(d["matched"] for d in dist_matches)

        batch.append({
            "store_id": store["store_id"],
            "brand": store.get("brand"),
            "store_name": store.get("store_name"),
            "address": store.get("address"),
            "phone": store.get("phone"),
            "email": store.get("email"),
            "parsed_country": store.get("parsed_country"),
            "parsed_city": store.get("parsed_city"),
            "house_number": store.get("house_number"),
            "road": store.get("road"),
            "zip": store.get("zip"),
            "state": store.get("state"),
            "is_chain": store.get("is_chain"),
            "chain_name": store.get("chain_name"),
            "sf_matched": sf_matched,
            "sf_account_id": sf_rec.source_id if sf_rec else None,
            "sf_account_name": sf_rec.name if sf_rec else None,
            "sf_match_layer": sf_layer if sf_matched else None,
            "sf_match_confidence": sf_conf if sf_matched else None,
            "sf_match_status": sf_status if sf_matched else None,
            "distributor_matches": dist_matches,
            "on_salesforce": sf_matched,
            "on_any_distributor": on_any_distributor,
            "place_id": None,
            "lat": None,
            "lng": None,
            "matched_at": now,
            "run_date": today,
        })

        if len(batch) == BATCH_SIZE:
            errors = client.insert_rows_json(table_ref, batch)
            if errors:
                raise RuntimeError(f"BigQuery insert errors at row {written}: {errors}")
            written += len(batch)
            batch = []
            dist_match_counts = {
                d["distributor_name"]: sum(1 for b in batch if any(
                    dm["distributor_name"] == d["distributor_name"] and dm["matched"]
                    for dm in b.get("distributor_matches", [])
                )) for d in dist_matches
            }
            print(f"[{written}/{total}] written | sf_matched={sf_matched_count} "
                  f"| {time.time() - t_start:.1f}s elapsed")

    if batch:
        errors = client.insert_rows_json(table_ref, batch)
        if errors:
            raise RuntimeError(f"BigQuery insert errors at row {written}: {errors}")
        written += len(batch)

    elapsed = time.time() - t_start
    print(f"Done — {written}/{total} records written to {table_ref} in {elapsed:.1f}s")
    print(f"Salesforce matched: {sf_matched_count}/{total}")
    for dist_name, dist_records, _, _ in dist_indexes:
        print(f"Distributor '{dist_name}': {len(dist_records)} records loaded")


# ---------------------------------------------------------------------------
# Entry point — HTTP trigger (called by Cloud Scheduler)
# ---------------------------------------------------------------------------

@functions_framework.http
def fn_retail_store_matching(request):
    client = bigquery.Client(project=PROJECT)
    _run_matching(client)
    return "OK", 200
